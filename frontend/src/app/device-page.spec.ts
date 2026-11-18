import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { BehaviorSubject } from 'rxjs';
import { afterEach, vi } from 'vitest';
import {
  DeviceApi,
  DeviceDetails,
  DeviceOperation,
  RegistrationLifetimeResult,
  ServerSetting,
  ServerSettingOperation,
} from './device-api';
import { DevicePage } from './device-page';

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
const lifetimeRead: RegistrationLifetimeResult = {
  task_id: 'lifetime-1',
  device_id: 'remote',
  resource: 'LwM2M Server.3.Lifetime',
  operation: 'read',
  state: 'succeeded',
  requested_lifetime: null,
  lifetime: 3600,
  matches_requested: null,
  observed_at: '2026-11-16T00:01:00Z',
};
const serverSettingDefinitions: Pick<
  ServerSetting,
  'resource_id' | 'label' | 'data_type' | 'value'
>[] = [
  { resource_id: 6, label: 'Notification storing', data_type: 'boolean', value: true },
  { resource_id: 7, label: 'Binding', data_type: 'string', value: 'U' },
  { resource_id: 14, label: 'Initial registration delay timer', data_type: 'integer', value: 0 },
  { resource_id: 17, label: 'Communication retry count', data_type: 'integer', value: 5 },
  { resource_id: 18, label: 'Communication retry timer', data_type: 'integer', value: 60 },
  {
    resource_id: 19,
    label: 'Communication sequence delay timer',
    data_type: 'integer',
    value: 86400,
  },
  { resource_id: 20, label: 'Communication sequence retry count', data_type: 'integer', value: 1 },
  { resource_id: 23, label: 'Mute Send', data_type: 'boolean', value: false },
];
const serverSettings: ServerSetting[] = serverSettingDefinitions.map((setting) => ({
  ...setting,
  resource: `LwM2M Server.3.Resource ${setting.resource_id}`,
  readable: true,
  writable: true,
  warning: 'Communication settings can interrupt connectivity.',
  minimum: setting.data_type === 'integer' ? 0 : null,
  maximum: setting.data_type === 'integer' ? 4294967295 : null,
  unit: null,
  observed_at: '2026-11-17T09:00:00Z',
}));
const pendingSetting: ServerSettingOperation = {
  id: 'setting-1',
  resource_id: 6,
  resource: 'LwM2M Server.3.Notification Storing',
  kind: 'write',
  state: 'pending',
  requested_value: false,
  value: null,
  observed_at: null,
  error: null,
};
const pendingTimezone: DeviceOperation = {
  id: 'timezone-1',
  resource_key: 'Device.0.Timezone',
  kind: 'read',
  requested_value: null,
  status: 'pending',
  value: null,
  error: null,
  created_at: '2026-11-16T00:00:00Z',
  completed_at: null,
};

describe('Device communication page', () => {
  afterEach(() => vi.useRealTimers());

  function setup() {
    const params = new BehaviorSubject(convertToParamMap({ endpoint: 'lab' }));
    const api = {
      detail: vi.fn().mockResolvedValue(detail),
      resources: vi.fn().mockResolvedValue({
        resources: [
          {
            key: 'Device.0.Timezone',
            resource_id: 15,
            data_type: 'String',
            operations: ['Read', 'Write'],
          },
        ],
      }),
      read: vi.fn().mockResolvedValue(pendingTimezone),
      operation: vi.fn().mockResolvedValue({
        ...pendingTimezone,
        status: 'succeeded',
        value: 'Etc/UTC',
        completed_at: '2026-11-16T00:01:00Z',
      }),
      writeTimezone: vi.fn().mockResolvedValue({
        ...pendingTimezone,
        id: 'timezone-write',
        kind: 'write',
        requested_value: 'Asia/Kolkata',
      }),
      readLifetime: vi.fn().mockResolvedValue(lifetimeRead),
      serverSettings: vi.fn().mockResolvedValue({
        settings: serverSettings,
        unresolved_operation: null,
      }),
      writeServerSetting: vi.fn().mockResolvedValue(pendingSetting),
      readServerSetting: vi.fn().mockResolvedValue({
        ...pendingSetting,
        kind: 'read',
        requested_value: null,
      }),
      serverSettingOperation: vi.fn().mockResolvedValue({
        ...pendingSetting,
        state: 'succeeded',
        value: false,
        observed_at: '2026-11-17T09:01:00Z',
      }),
      writeLifetime: vi.fn().mockImplementation((_endpoint: string, lifetime: number) =>
        Promise.resolve({
          ...lifetimeRead,
          operation: 'write',
          requested_lifetime: lifetime,
          lifetime,
          matches_requested: true,
        }),
      ),
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

  it('automatically shows all nine server settings without a load button or device commands', async () => {
    const { fixture, api, element } = setup();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(api.detail).toHaveBeenCalledWith('lab');
    expect(api.resources).toHaveBeenCalledWith('lab');
    expect(api.read).not.toHaveBeenCalled();
    expect(api.readLifetime).not.toHaveBeenCalled();
    expect(api.writeLifetime).not.toHaveBeenCalled();
    expect(api.serverSettings).toHaveBeenCalledExactlyOnceWith('lab');
    expect(api.writeServerSetting).not.toHaveBeenCalled();
    expect(api.readServerSetting).not.toHaveBeenCalled();
    expect(element.querySelectorAll('#lwm2m-server-content .settings-card')).toHaveLength(9);
    expect(element.textContent).not.toContain('Load server settings');
    expect(element.textContent).not.toContain('Reload server settings');
    expect(element.textContent).toContain('Lab sensor');
    expect(
      element.querySelector<HTMLButtonElement>(
        '.lifetime-card .configuration-editor .button.primary',
      )?.disabled,
    ).toBe(true);
  });

  it('retains the timezone read-before-write flow beside the server settings', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    await page.writeTimezone();
    expect(api.writeTimezone).not.toHaveBeenCalled();

    await page.readResource(page.timezoneKey);
    await page.checkResult(page.timezoneKey);
    expect(page.timezoneForm.controls.timezone.value).toBe('Etc/UTC');
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    await page.writeTimezone();
    expect(api.writeTimezone).toHaveBeenCalledWith('lab', 'Asia/Kolkata');
    expect(page.readings()[page.timezoneKey]?.value).toBe('Etc/UTC');

    api.operation.mockResolvedValueOnce({
      ...pendingTimezone,
      id: 'timezone-write',
      kind: 'write',
      requested_value: 'Asia/Kolkata',
      status: 'succeeded',
      value: 'Asia/Kolkata',
      completed_at: '2026-11-16T00:02:00Z',
    });
    await page.checkResult(page.timezoneKey);
    fixture.detectChanges();
    expect(element.querySelector('.timezone-card strong')?.textContent).toBe('Asia/Kolkata');
    expect(page.operationLabel(page.timezoneKey)).toBe('Applied and verified');
    expect(api.writeServerSetting).not.toHaveBeenCalled();
  });

  it('does not present a mismatched timezone write as verified', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    await page.readResource(page.timezoneKey);
    await page.checkResult(page.timezoneKey);
    page.timezoneForm.controls.timezone.setValue('Asia/Kolkata');
    await page.writeTimezone();
    api.operation.mockResolvedValueOnce({
      ...pendingTimezone,
      id: 'timezone-write',
      kind: 'write',
      requested_value: 'Asia/Kolkata',
      status: 'succeeded',
      value: 'Etc/UTC',
      completed_at: '2026-11-16T00:02:00Z',
    });
    await page.checkResult(page.timezoneKey);
    expect(page.readings()[page.timezoneKey]?.value).toBe('Etc/UTC');
    expect(page.unconfirmed()[page.timezoneKey]).toBe(true);
  });

  it('omits Attributes and shows registration-lifetime guidance and controls', async () => {
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
    expect(settingTitles).toEqual([
      'Registration lifetime',
      ...serverSettings.map((s) => s.label),
      'Timezone',
    ]);
    expect(element.querySelector('#lwm2m-server-heading')?.textContent).toBe('LwM2M server');
    expect(element.querySelector('#settings')?.textContent).toContain('Timezone');
    expect(element.querySelector('#settings')?.textContent).toContain('Read timezone');
    expect(element.querySelector('#settings')?.textContent).not.toContain('Check result');
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
    expect(api.readLifetime).not.toHaveBeenCalled();
    expect(api.writeLifetime).not.toHaveBeenCalled();
    expect(card.textContent).toContain('latest confirmed value');
    expect(card.textContent).toContain('stored confirmed value');
    const input = card.querySelector<HTMLInputElement>('input')!;
    expect(input.value).toBe('');
    input.value = '120';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
    expect(page.lifetimeDraft.value).toBe(120);
    const apply = card.querySelector<HTMLButtonElement>('.configuration-editor .button.primary')!;
    expect(apply.disabled).toBe(false);
    apply.click();
    await fixture.whenStable();
    expect(api.writeLifetime).toHaveBeenCalledWith('lab', 120);
    expect(card.textContent).toContain('Registration lifetime applied and verified on the device.');
    expect(card.textContent).toContain('120 seconds');
    expect(info.textContent).toContain('registration validity period');
    card.querySelector<HTMLButtonElement>('.button.quiet')!.click();
    fixture.detectChanges();
    expect(page.lifetimeDraft.value).toBeNull();
    expect(input.value).toBe('');
  });

  it('loads the latest confirmed lifetime only after the user requests it', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    const read = Array.from(
      element.querySelectorAll<HTMLButtonElement>('.lifetime-card button'),
    ).find((button) => button.textContent?.includes('Show current lifetime'))!;
    read.click();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(api.readLifetime).toHaveBeenCalledWith('lab');
    expect(page.lifetimeResult()?.lifetime).toBe(3600);
    expect(element.querySelector('.lifetime-current strong')?.textContent).toContain(
      '3600 seconds',
    );
    expect(element.querySelector('.lifetime-current .reading-time')?.textContent).toContain('UTC');
  });

  it('shows a verified lifetime mismatch without claiming the write succeeded', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    page.lifetimeDraft.setValue(7200);
    api.writeLifetime.mockRejectedValueOnce(
      new HttpErrorResponse({
        status: 409,
        error: {
          ...lifetimeRead,
          operation: 'write',
          state: 'verification_failed',
          requested_lifetime: 7200,
          lifetime: 3600,
          matches_requested: false,
        },
      }),
    );
    await page.writeLifetime();
    expect(page.lifetimeResult()?.lifetime).toBe(3600);
    expect(page.lifetimeError()).toContain('3600 seconds');
    expect(page.lifetimeStatus()).not.toContain('applied and verified');
  });

  it('updates lifetime action buttons after typing without forcing change detection', async () => {
    const { fixture, api, element } = setup();
    await fixture.whenStable();
    const input = element.querySelector<HTMLInputElement>('#lifetime-seconds')!;
    const apply = element.querySelector<HTMLButtonElement>('.lifetime-card .button.primary')!;
    const discard = element.querySelector<HTMLButtonElement>('.lifetime-card .button.quiet')!;
    expect(apply.disabled).toBe(true);
    expect(discard.disabled).toBe(true);
    input.value = '120';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await fixture.whenStable();
    expect(apply.disabled).toBe(false);
    expect(discard.disabled).toBe(false);
    discard.click();
    await fixture.whenStable();
    expect(input.value).toBe('');
    expect(apply.disabled).toBe(true);
    expect(discard.disabled).toBe(true);
    expect(api.writeLifetime).not.toHaveBeenCalled();
  });

  it('enables all eight setting actions for valid inputs without an acknowledgement checkbox or forced change detection', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    await page.loadServerSettings();
    await fixture.whenStable();
    expect(element.querySelector('#lwm2m-server-content input[type="checkbox"]')).toBeNull();
    const cards = Array.from(element.querySelectorAll<HTMLElement>('.server-setting-card'));
    for (const card of cards) {
      const input = card.querySelector<HTMLInputElement>('input');
      const select = card.querySelector<HTMLSelectElement>('select');
      if (input) {
        input.value = input.type === 'number' ? '120' : 'UQ';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      } else if (select) {
        select.selectedIndex = 2;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    await fixture.whenStable();
    for (const card of cards) {
      expect(card.querySelector<HTMLButtonElement>('.button.quiet')!.disabled).toBe(false);
      expect(card.querySelector<HTMLButtonElement>('.button.primary')!.disabled).toBe(false);
      card.querySelector<HTMLButtonElement>('.button.quiet')!.click();
    }
    await fixture.whenStable();
    for (const card of cards) {
      expect(card.querySelector<HTMLButtonElement>('.button.primary')!.disabled).toBe(true);
      expect(card.querySelector<HTMLButtonElement>('.button.quiet')!.disabled).toBe(true);
    }
    expect(api.writeServerSetting).not.toHaveBeenCalled();
  });

  it('blocks duplicate lifetime writes when the backend cannot confirm the outcome', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    api.writeLifetime.mockRejectedValue(
      new HttpErrorResponse({
        status: 504,
        error: { detail: 'A submitted write may still execute later.' },
      }),
    );
    page.lifetimeDraft.setValue(7200);
    await page.writeLifetime();
    expect(page.lifetimeWriteUnconfirmed()).toBe(true);
    expect(page.lifetimeError()).toContain('Do not retry');
    await page.writeLifetime();
    expect(api.writeLifetime).toHaveBeenCalledTimes(1);
  });

  it('loads all eight extra settings with typed controls and no executable commands', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    await page.loadServerSettings();
    fixture.detectChanges();
    expect(api.serverSettings).toHaveBeenCalledWith('lab');
    expect(element.querySelectorAll('.server-setting-card')).toHaveLength(8);
    expect(element.querySelectorAll('.server-setting-card select')).toHaveLength(2);
    expect(element.querySelectorAll('.server-setting-card input[type="number"]')).toHaveLength(5);
    expect(element.querySelectorAll('.server-setting-card input[type="text"]')).toHaveLength(1);
    expect(element.textContent).not.toContain('Bootstrap');
    expect(element.querySelector('.timezone-card')?.textContent).toContain('Timezone');
    const setting = page.serverSettings()[7];
    expect(setting.value).toBe(false);
    expect(element.querySelector('[data-resource-id="23"] strong')?.textContent).toContain('false');
    expect(api.writeServerSetting).not.toHaveBeenCalled();
    const notification = element.querySelector<HTMLSelectElement>('[data-resource-id="6"] select')!;
    notification.selectedIndex = 2;
    notification.dispatchEvent(new Event('change', { bubbles: true }));
    expect(page.serverDraft(page.serverSettings()[0]).value).toBe(false);
    const retry = element.querySelector<HTMLInputElement>('[data-resource-id="18"] input')!;
    retry.value = '120';
    retry.dispatchEvent(new Event('input', { bubbles: true }));
    expect(page.serverDraft(page.serverSettings()[4]).value).toBe(120);
  });

  it('submits a valid boolean without acknowledgement and automatically verifies it without resubmitting', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    vi.useFakeTimers();
    await page.loadServerSettings();
    const setting = page.serverSettings()[0];
    page.serverDraft(setting).setValue(false);
    await page.submitServerSetting(setting, 'write');
    expect(api.writeServerSetting).toHaveBeenCalledWith('lab', 6, false);
    expect(page.serverSettings()[0].value).toBe(true);
    expect(api.serverSettingOperation).not.toHaveBeenCalled();
    await page.submitServerSetting(setting, 'write');
    expect(api.writeServerSetting).toHaveBeenCalledTimes(1);
    expect(page.serverAutoChecking()).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    fixture.detectChanges();
    expect(api.serverSettingOperation).toHaveBeenCalledExactlyOnceWith('lab', pendingSetting.id);
    expect(api.writeServerSetting).toHaveBeenCalledTimes(1);
    expect(page.serverSettings()[0].value).toBe(false);
    expect(element.textContent).toContain('Applied and verified on the device');
    expect(element.textContent).toMatch(/Read-back:\s+false at/);
    expect(page.serverSettingsBusy()).toBe(false);
    expect(page.serverAutoChecking()).toBe(false);
  });

  it('groups all nine settings in a collapsible region and preserves drafts and result checks while collapsed', async () => {
    const { fixture, page, api, element, params } = setup();
    await fixture.whenStable();
    vi.useFakeTimers();
    page.lifetimeDraft.setValue(120);
    const notification = page.serverSettings()[0];
    page.serverDraft(notification).setValue(false);
    await page.submitServerSetting(notification, 'write');
    fixture.detectChanges();
    const content = element.querySelector<HTMLElement>('#lwm2m-server-content')!;
    const toggle = element.querySelector<HTMLButtonElement>('.server-group-toggle')!;
    expect(content.querySelectorAll('.settings-card')).toHaveLength(9);
    expect(content.querySelector('.lifetime-card')).not.toBeNull();
    expect(toggle.getAttribute('aria-controls')).toBe(content.id);
    toggle.click();
    fixture.detectChanges();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.getAttribute('aria-label')).toBe('Expand LwM2M server');
    expect(content.hidden).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    expect(page.serverOperation()?.state).toBe('succeeded');
    toggle.click();
    fixture.detectChanges();
    expect(content.hidden).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(page.lifetimeDraft.value).toBe(120);
    expect(page.serverDraft(notification).value).toBe(false);
    expect(api.writeServerSetting).toHaveBeenCalledTimes(1);
    expect(api.writeLifetime).not.toHaveBeenCalled();
    expect(api.readServerSetting).not.toHaveBeenCalled();
    expect(api.serverSettings).toHaveBeenCalledTimes(1);
    toggle.click();
    params.next(convertToParamMap({ endpoint: 'other' }));
    fixture.detectChanges();
    expect(content.hidden).toBe(false);
    expect(page.lifetimeDraft.value).toBeNull();
  });

  it('unblocks settings when reload reconciles a completed operation', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    vi.useFakeTimers();
    api.serverSettings.mockResolvedValueOnce({
      settings: serverSettings,
      unresolved_operation: pendingSetting,
    });
    await page.loadServerSettings();
    expect(page.serverSettingsBusy()).toBe(true);
    api.serverSettings.mockResolvedValueOnce({
      settings: serverSettings,
      unresolved_operation: null,
      completed_operation: {
        ...pendingSetting,
        state: 'succeeded',
        value: false,
        observed_at: '2026-11-17T09:01:00Z',
      },
    });
    await page.loadServerSettings();
    expect(page.serverOperation()?.state).toBe('succeeded');
    expect(page.serverSettings()[0].value).toBe(false);
    expect(page.serverSettingsBusy()).toBe(false);
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.serverSettingOperation).not.toHaveBeenCalled();
    expect(api.writeServerSetting).not.toHaveBeenCalled();
  });

  it('bounds pending result checks and cancels them on navigation and destruction without retrying writes', async () => {
    const { fixture, page, api, params } = setup();
    await fixture.whenStable();
    vi.useFakeTimers();
    api.serverSettings.mockResolvedValue({
      settings: serverSettings,
      unresolved_operation: pendingSetting,
    });
    api.serverSettingOperation.mockResolvedValue(pendingSetting);
    await page.loadServerSettings();
    await vi.advanceTimersByTimeAsync(120000);
    fixture.detectChanges();
    expect(api.serverSettingOperation).toHaveBeenCalledTimes(30);
    expect(page.serverAutoChecking()).toBe(false);
    expect(page.serverSettingsBusy()).toBe(true);
    expect(fixture.nativeElement.textContent).toContain('Automatic checks paused');
    await page.loadServerSettings();
    api.serverSettings.mockResolvedValueOnce({
      settings: serverSettings,
      unresolved_operation: null,
    });
    params.next(convertToParamMap({ endpoint: 'other' }));
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.serverSettingOperation).toHaveBeenCalledTimes(30);
    await page.loadServerSettings();
    fixture.destroy();
    await vi.advanceTimersByTimeAsync(2000);
    expect(api.serverSettingOperation).toHaveBeenCalledTimes(30);
    expect(api.writeServerSetting).not.toHaveBeenCalled();
  });

  it('surfaces automatic check failures and stops checking without unlocking or retrying', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    vi.useFakeTimers();
    api.serverSettings.mockResolvedValue({
      settings: serverSettings,
      unresolved_operation: pendingSetting,
    });
    api.serverSettingOperation.mockRejectedValue(
      new HttpErrorResponse({ status: 502, error: { detail: 'Connector unavailable' } }),
    );
    await page.loadServerSettings();
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.serverSettingOperation).toHaveBeenCalledTimes(1);
    expect(page.serverSettingsError()).toBe('Connector unavailable');
    expect(page.serverAutoChecking()).toBe(false);
    expect(page.serverSettingsBusy()).toBe(true);
    expect(api.writeServerSetting).not.toHaveBeenCalled();
  });

  it('validates numeric and binding drafts before calling the backend', async () => {
    const { fixture, page, api } = setup();
    await fixture.whenStable();
    await page.loadServerSettings();
    const retry = page.serverSettings()[4];
    for (const value of [-1, 1.5, 4294967296, '60']) {
      page.serverDraft(retry).setValue(value);
      await page.submitServerSetting(retry, 'write');
    }
    const binding = page.serverSettings()[1];
    page.serverDraft(binding).setValue('UU');
    await page.submitServerSetting(binding, 'write');
    expect(api.writeServerSetting).not.toHaveBeenCalled();
    page.serverDraft(retry).setValue(0);
    expect(page.validServerDraft(retry)).toBe(true);
    page.serverDraft(binding).setValue('U');
    expect(page.validServerDraft(binding)).toBe(true);
  });

  it('recovers persisted pending settings and preserves a mismatching device value without claiming success', async () => {
    const { fixture, page, api, element } = setup();
    await fixture.whenStable();
    api.serverSettings.mockResolvedValue({
      settings: serverSettings,
      unresolved_operation: pendingSetting,
    });
    await page.loadServerSettings();
    expect(page.serverSettingsBusy()).toBe(true);
    api.serverSettingOperation.mockResolvedValue({
      ...pendingSetting,
      state: 'verification_failed',
      value: true,
      observed_at: '2026-11-17T09:01:00Z',
      error: 'Read-back differs',
    });
    await page.checkServerSetting();
    fixture.detectChanges();
    expect(page.serverSettings()[0].value).toBe(true);
    expect(element.textContent).toContain('Read-back differs');
    expect(element.textContent).not.toContain('Applied and verified on the device');
  });

  it('keeps lost setting submissions blocked and ignores responses after navigation', async () => {
    const { fixture, page, api, params } = setup();
    await fixture.whenStable();
    await page.loadServerSettings();
    const setting = page.serverSettings()[0];
    page.serverDraft(setting).setValue(false);
    api.writeServerSetting.mockRejectedValue(new HttpErrorResponse({ status: 0 }));
    await page.submitServerSetting(setting, 'write');
    expect(page.serverSettingsBusy()).toBe(true);
    await page.submitServerSetting(setting, 'write');
    expect(api.writeServerSetting).toHaveBeenCalledTimes(1);
    let resolve!: (value: { settings: ServerSetting[]; unresolved_operation: null }) => void;
    api.serverSettings.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );
    api.serverSettings.mockResolvedValueOnce({ settings: [], unresolved_operation: null });
    const loading = page.loadServerSettings();
    params.next(convertToParamMap({ endpoint: 'other' }));
    resolve({ settings: serverSettings, unresolved_operation: null });
    await loading;
    expect(page.serverSettings()).toEqual([]);
    expect(page.serverOperation()).toBeNull();
    expect(api.serverSettings).toHaveBeenLastCalledWith('other');
  });

  it('ignores delayed device responses after navigating to another endpoint', async () => {
    const { fixture, page, api, params } = setup();
    await fixture.whenStable();
    let resolve!: (value: RegistrationLifetimeResult) => void;
    api.readLifetime.mockReturnValue(
      new Promise<RegistrationLifetimeResult>((r) => {
        resolve = r;
      }),
    );
    const reading = page.readLifetime();
    page.lifetimeDraft.setValue(120);
    params.next(convertToParamMap({ endpoint: 'other' }));
    resolve(lifetimeRead);
    await reading;
    await fixture.whenStable();
    expect(page.endpoint()).toBe('other');
    expect(page.lifetimeResult()).toBeNull();
    expect(page.lifetimeDraft.value).toBeNull();
    expect(api.detail).toHaveBeenLastCalledWith('other');
  });
});
