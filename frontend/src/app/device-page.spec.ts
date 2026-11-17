import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { vi } from 'vitest';
import { DeviceApi, DeviceDetails, DeviceOperation, DeviceResource } from './device-api';
import { DevicePage } from './device-page';

const timezone = 'Device.0.Timezone';
const resources: DeviceResource[] = [
  { key: timezone, resource_id: 15, data_type: 'String', operations: ['Read', 'Write'] },
];
const detail: DeviceDetails = {
  local: {
    id: 1,
    endpoint_name: 'lab',
    friendly_name: 'Lab sensor',
    coiote_device_id: 'remote',
    security_mode: 'psk',
    onboarding_status: 'coiote_record_created',
    connection_status: 'unknown',
    refreshed_at: null,
    first_registered_at: null,
    last_registered_at: null,
    last_seen_at: null,
    created_at: null,
  },
  coiote_snapshot: null,
};
const pending: DeviceOperation = {
  id: 'read-1',
  kind: 'read',
  resource_key: timezone,
  requested_value: null,
  status: 'pending',
  value: null,
  error: null,
  created_at: '2026-11-16T00:00:00Z',
  completed_at: null,
};
const succeeded: DeviceOperation = {
  ...pending,
  status: 'succeeded',
  value: 'Etc/UTC',
  completed_at: '2026-11-16T00:01:00Z',
};

describe('Device communication page', () => {
  function setup() {
    const params = new BehaviorSubject(convertToParamMap({ endpoint: 'lab' }));
    const api = {
      detail: vi.fn().mockResolvedValue(detail),
      resources: vi.fn().mockResolvedValue({ resources }),
      read: vi.fn().mockResolvedValue(pending),
      writeTimezone: vi.fn().mockResolvedValue({
        ...pending,
        id: 'write-1',
        kind: 'write',
        requested_value: 'Asia/Kolkata',
      }),
      operation: vi.fn().mockResolvedValue(succeeded),
    };
    TestBed.configureTestingModule({
      imports: [DevicePage],
      providers: [
        provideRouter([]),
        { provide: ActivatedRoute, useValue: { paramMap: params } },
        { provide: DeviceApi, useValue: api },
      ],
    });
    const fixture = TestBed.createComponent(DevicePage);
    fixture.detectChanges();
    return {
      fixture,
      page: fixture.componentInstance,
      api,
      params,
      element: fixture.nativeElement as HTMLElement,
    };
  }

  it('loads administrative details and stored capabilities without sending live commands', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(api.detail).toHaveBeenCalledWith('lab');
    expect(api.resources).toHaveBeenCalledWith('lab');
    expect(api.read).not.toHaveBeenCalled();
    expect(api.writeTimezone).not.toHaveBeenCalled();
    expect(element.textContent).toContain('Lab sensor');
    expect(page.readings()).toEqual({});
    expect(element.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  });

  it('omits Attributes while keeping settings and Lifetime editing UI-only', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(element.querySelector('#attributes')).toBeNull();
    expect(element.querySelector('a[href$="#attributes"]')).toBeNull();
    expect(element.querySelector('#settings h2')?.textContent).toContain(
      'Settings & configuration',
    );
    const settingTitles = Array.from(
      element.querySelectorAll('#settings .configuration-title'),
    ).map((heading) => heading.textContent?.trim());
    expect(settingTitles).toEqual(['Registration lifetime', 'Timezone']);
    expect(element.textContent).not.toContain('Manufacturer');
    expect(element.textContent).not.toContain('Model number');
    expect(element.textContent).not.toContain('Firmware version');
    const card = element.querySelector<HTMLElement>('.lifetime-card')!;
    expect(card.textContent).not.toContain('UI only');
    const info = card.querySelector<HTMLElement>('.lifetime-info-panel')!;
    expect(info.hidden).toBe(true);
    const infoToggle = card.querySelector<HTMLButtonElement>('.lifetime-info-toggle')!;
    expect(infoToggle.getAttribute('aria-label')).toBe('About registration lifetime');
    expect(infoToggle.textContent?.trim()).toBe('');
    infoToggle.click();
    fixture.detectChanges();
    expect(info.hidden).toBe(false);
    expect(infoToggle.getAttribute('aria-expanded')).toBe('true');
    infoToggle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(info.hidden).toBe(true);
    const input = card.querySelector<HTMLInputElement>('input')!;
    expect(input.closest('form')).toBeNull();
    expect(input.value).toBe('');
    input.value = '120';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
    expect(page.lifetimeDraft.value).toBe(120);
    const apply = card.querySelector<HTMLButtonElement>('.button.primary')!;
    expect(apply.disabled).toBe(true);
    apply.click();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await fixture.whenStable();
    expect(api.read).not.toHaveBeenCalled();
    expect(api.writeTimezone).not.toHaveBeenCalled();
    expect(api.operation).not.toHaveBeenCalled();
    expect(card.textContent).toContain('Not read from, saved to or sent to the device');
    expect(info.textContent).toContain('registration validity period');
    card.querySelector<HTMLButtonElement>('.button.quiet')!.click();
    fixture.detectChanges();
    expect(page.lifetimeDraft.value).toBeNull();
    expect(input.value).toBe('');
  });

  it('manually reads a value and checks its result without polling or resubmitting', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    await page.readResource(timezone);
    await page.readResource(timezone);
    expect(api.read).toHaveBeenCalledTimes(1);
    expect(page.readings()[timezone]).toBeUndefined();
    expect(api.operation).not.toHaveBeenCalled();
    fixture.detectChanges();
    const check = Array.from(element.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.trim() === 'Check result',
    )!;
    check.click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(api.operation).toHaveBeenCalledWith('lab', 'read-1');
    expect(element.querySelector('.current-setting strong')?.textContent).toBe('Etc/UTC');
    expect(page.unresolved(timezone)).toBe(false);
  });

  it('requires an initial live timezone Read and waits for verified Write-back before updating', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    await page.writeTimezone();
    expect(api.writeTimezone).not.toHaveBeenCalled();
    await page.readResource(timezone);
    await page.checkResult(timezone);
    expect(page.timezoneForm.controls.timezone.value).toBe('Etc/UTC');
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    fixture.detectChanges();
    const form = element.querySelector('form')!;
    const event = new Event('submit', { bubbles: true, cancelable: true });
    form.dispatchEvent(event);
    await fixture.whenStable();
    expect(event.defaultPrevented).toBe(true);
    expect(api.writeTimezone).toHaveBeenCalledWith('lab', 'Asia/Kolkata');
    expect(page.readings()[timezone]?.value).toBe('Etc/UTC');
    expect(page.operationLabel(timezone)).not.toContain('Applied');
    await page.writeTimezone();
    expect(api.writeTimezone).toHaveBeenCalledTimes(1);
    api.operation.mockResolvedValue({
      ...succeeded,
      id: 'write-1',
      kind: 'write',
      requested_value: 'Asia/Kolkata',
      value: 'Asia/Kolkata',
    });
    await page.checkResult(timezone);
    expect(page.readings()[timezone]?.value).toBe('Asia/Kolkata');
    expect(page.operationLabel(timezone)).toBe('Applied and verified');
    expect(page.timezoneForm.pristine).toBe(true);
  });

  it('retains the last verified timezone on a failed Write and preserves dirty edits during Reads', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    await page.readResource(timezone);
    await page.checkResult(timezone);
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    page.timezoneForm.controls.timezone.markAsDirty();
    await page.readResource(timezone);
    await page.checkResult(timezone);
    expect(page.timezoneForm.controls.timezone.value).toBe('Asia/Kolkata');
    await page.writeTimezone();
    api.operation.mockResolvedValue({
      ...pending,
      id: 'write-1',
      kind: 'write',
      requested_value: 'Asia/Kolkata',
      status: 'failed',
      value: 'Europe/Warsaw',
      error: 'Read-back differs from requested value',
    });
    await page.checkResult(timezone);
    expect(page.readings()[timezone]?.value).toBe('Etc/UTC');
    expect(page.operations()[timezone]?.error).toContain('Read-back differs');
    expect(page.operationLabel(timezone)).not.toContain('Applied');
  });

  it('blocks uncertain Writes and recovers a known operation through manual result checks', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    await page.readResource(timezone);
    await page.checkResult(timezone);
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    api.writeTimezone.mockRejectedValue(
      new HttpErrorResponse({
        status: 502,
        error: { detail: { message: 'Submission unconfirmed', operation_id: 'write-1' } },
      }),
    );
    await page.writeTimezone();
    expect(page.operationIds()[timezone]).toBe('write-1');
    expect(page.errors()[timezone]).toBe('Submission unconfirmed');
    await page.writeTimezone();
    expect(api.writeTimezone).toHaveBeenCalledTimes(1);
    api.operation.mockRejectedValue(new HttpErrorResponse({ status: 502 }));
    await page.checkResult(timezone);
    expect(page.busy(timezone)).toBe(true);
    api.operation.mockResolvedValue({
      ...pending,
      id: 'write-1',
      kind: 'write',
      status: 'submission_unknown',
      requested_value: 'Asia/Kolkata',
      error: 'No confirmed provider task ID',
    });
    await page.checkResult(timezone);
    expect(page.unresolved(timezone)).toBe(true);
    expect(page.readings()[timezone]?.value).toBe('Etc/UTC');
  });

  it('does not check an earlier operation ID after a new submission loses its response', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    await page.readResource(timezone);
    await page.checkResult(timezone);
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    api.writeTimezone.mockRejectedValue(new HttpErrorResponse({ status: 0 }));
    await page.writeTimezone();
    expect(page.operationIds()[timezone]).toBeUndefined();
    await page.writeTimezone();
    expect(api.writeTimezone).toHaveBeenCalledTimes(1);
    await page.checkResult(timezone);
    expect(api.operation).toHaveBeenCalledTimes(1);
    expect(page.errors()[timezone]).toContain('No confirmed operation ID');
  });

  it('keeps details visible if resource discovery fails and disables unsupported commands', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    api.resources.mockRejectedValue(
      new HttpErrorResponse({
        status: 502,
        error: { detail: 'Resource discovery failed' },
      }),
    );
    await page.loadResources();
    await page.readResource(timezone);
    fixture.detectChanges();
    expect(element.textContent).toContain('Lab sensor');
    expect(element.textContent).toContain('Resource discovery failed');
    expect(api.read).not.toHaveBeenCalled();
    expect(page.supports(timezone, 'Write')).toBe(false);
  });

  it('ignores delayed device responses after navigating to another endpoint', async () => {
    const { fixture, page, api, params } = setup();
    await fixture.whenStable();
    let resolve!: (value: DeviceOperation) => void;
    api.read.mockReturnValue(
      new Promise<DeviceOperation>((r) => {
        resolve = r;
      }),
    );
    const reading = page.readResource(timezone);
    page.lifetimeDraft.setValue(120);
    params.next(convertToParamMap({ endpoint: 'other' }));
    resolve(succeeded);
    await reading;
    await fixture.whenStable();
    expect(page.endpoint()).toBe('other');
    expect(page.readings()).toEqual({});
    expect(page.operations()).toEqual({});
    expect(page.lifetimeDraft.value).toBeNull();
    expect(api.resources).toHaveBeenLastCalledWith('other');
  });
});
