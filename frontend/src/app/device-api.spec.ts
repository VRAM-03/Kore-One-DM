import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import {
  DeviceApi,
  errorMessage,
  isRegistrationLifetimeResult,
  submissionOperationId,
} from './device-api';

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

  it('fetches the activity estimate without triggering inventory sync', async () => {
    const activity = firstValueFrom(api.activity());
    const request = http.expectOne('/api/device-activity');
    expect(request.request.method).toBe('GET');
    request.flush({ recently_active: 1, unknown: 2, checked_at: '2026-11-18T00:00:00Z' });
    expect(await activity).toEqual({
      recently_active: 1,
      unknown: 2,
      checked_at: '2026-11-18T00:00:00Z',
    });
  });

  it('maps registration-lifetime reads and verified writes to their existing routes', async () => {
    const endpoint = 'sensor ?#';
    const url = '/api/devices/sensor%20%3F%23';
    const read = api.readLifetime(endpoint);
    const reading = http.expectOne(`${url}/lifetime/read`);
    expect(reading.request.method).toBe('POST');
    expect(reading.request.body).toBeNull();
    reading.flush({
      task_id: 'read-1',
      device_id: 'remote',
      resource: 'LwM2M Server.3.Lifetime',
      operation: 'read',
      state: 'succeeded',
      requested_lifetime: null,
      lifetime: 3600,
      matches_requested: null,
      observed_at: '2026-11-16T00:01:00Z',
    });
    await read;

    const write = api.writeLifetime(endpoint, 7200);
    const writing = http.expectOne(`${url}/lifetime`);
    expect(writing.request.method).toBe('PATCH');
    expect(writing.request.body).toEqual({ lifetime: 7200 });
    writing.flush({});
    await write;
  });

  it('recognizes a verified lifetime mismatch returned with HTTP 409', () => {
    expect(
      isRegistrationLifetimeResult({
        task_id: 'write-1',
        device_id: 'remote',
        resource: 'LwM2M Server.3.Lifetime',
        operation: 'write',
        state: 'verification_failed',
        requested_lifetime: 7200,
        lifetime: 3600,
        matches_requested: false,
        observed_at: '2026-11-16T00:01:00Z',
      }),
    ).toBe(true);
    expect(isRegistrationLifetimeResult({ state: 'verification_failed', lifetime: '3600' })).toBe(
      false,
    );
  });

  it('uses backend-only server setting routes and preserves boolean, integer and binding types', async () => {
    const url = '/api/devices/sensor%20%3F%23/server-settings';
    const loading = api.serverSettings('sensor ?#');
    const list = http.expectOne(url);
    expect(list.request.method).toBe('GET');
    list.flush({ settings: [], unresolved_operation: null });
    await loading;
    for (const [resourceId, value] of [
      [6, false],
      [18, 60],
      [7, 'U'],
    ] as const) {
      const applying = api.writeServerSetting('sensor ?#', resourceId, value);
      const write = http.expectOne(`${url}/${resourceId}`);
      expect(write.request.method).toBe('PATCH');
      expect(write.request.body).toEqual({ value });
      write.flush({});
      await applying;
    }
    const reading = api.readServerSetting('sensor ?#', 23);
    const read = http.expectOne(`${url}/23/read`);
    expect(read.request.method).toBe('POST');
    expect(read.request.body).toBeNull();
    read.flush({});
    await reading;
    const checking = api.serverSettingOperation('sensor ?#', 'operation ?#');
    const check = http.expectOne(`${url}/operations/operation%20%3F%23`);
    expect(check.request.method).toBe('GET');
    check.flush({});
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
