import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { DeviceApi, errorMessage, submissionOperationId } from './device-api';

describe('Existing NEXYN API integration', () => {
  let api: DeviceApi;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(DeviceApi);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => http.verify());

  it('uses only existing device APIs with correctly encoded identities and payloads', async () => {
    const endpoint = 'sensor with space?#';
    const url = '/api/devices/sensor%20with%20space%3F%23';
    const listed = api.list();
    http.expectOne('/api/devices').flush([]);
    await listed;
    const detail = api.detail(endpoint);
    http.expectOne(url).flush({ local: {}, coiote_snapshot: null });
    await detail;
    const created = api.create({ endpoint_name: endpoint, security_mode: 'nosec' });
    const create = http.expectOne('/api/devices');
    expect(create.request.method).toBe('POST');
    expect(create.request.body).toEqual({ endpoint_name: endpoint, security_mode: 'nosec' });
    create.flush({});
    await created;
    const renamed = api.rename(endpoint, 'New name');
    const rename = http.expectOne(url);
    expect(rename.request.method).toBe('PATCH');
    expect(rename.request.body).toEqual({ friendly_name: 'New name' });
    rename.flush({});
    await renamed;
    const deleted = api.delete(endpoint);
    const deletion = http.expectOne(url);
    expect(deletion.request.method).toBe('DELETE');
    deletion.flush(null, { status: 204, statusText: 'No Content' });
    await deleted;
  });

  it('shows validation messages without serializing submitted credentials', () => {
    const error = new HttpErrorResponse({
      status: 422,
      error: {
        detail: [{ msg: 'Invalid PSK format', input: 'SECRET', ctx: { value: 'SECRET' } }],
      },
    });
    expect(errorMessage(error)).toBe('Invalid PSK format');
    expect(errorMessage(error)).not.toContain('SECRET');
  });

  it('maps discovery, live Read, timezone Write and result checks to their exact APIs', async () => {
    const endpoint = 'sensor ?#';
    const url = '/api/devices/sensor%20%3F%23';
    const discovery = api.resources(endpoint);
    const resources = http.expectOne(`${url}/resources`);
    expect(resources.request.method).toBe('GET');
    resources.flush({ resources: [] });
    await discovery;
    const reading = api.read(endpoint, 'Device.0.Manufacturer');
    const read = http.expectOne(`${url}/reads`);
    expect(read.request.method).toBe('POST');
    expect(read.request.body).toEqual({ resource_key: 'Device.0.Manufacturer' });
    read.flush({});
    await reading;
    const writing = api.writeTimezone(endpoint, 'Etc/UTC');
    const write = http.expectOne(`${url}/writes`);
    expect(write.request.method).toBe('POST');
    expect(write.request.body).toEqual({ resource_key: 'Device.0.Timezone', value: 'Etc/UTC' });
    write.flush({});
    await writing;
    const checking = api.operation(endpoint, 'operation ?#');
    const operation = http.expectOne(`${url}/operations/operation%20%3F%23`);
    expect(operation.request.method).toBe('GET');
    operation.flush({});
    await checking;
  });

  it('extracts a sanitized submission message and operation ID without leaking raw data', () => {
    const error = new HttpErrorResponse({
      status: 502,
      error: {
        detail: { message: 'Submission unconfirmed', operation_id: 'known-id', raw: 'SECRET' },
      },
    });
    expect(errorMessage(error)).toBe('Submission unconfirmed');
    expect(submissionOperationId(error)).toBe('known-id');
    expect(submissionOperationId(new HttpErrorResponse({ status: 0 }))).toBeNull();
  });
});
