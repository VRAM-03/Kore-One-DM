import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import {
  DeviceApi,
  DeviceDetails,
  DeviceLastSeen,
  DeviceSetting,
  RegistrationLifetimeResult,
  ServerSetting,
  ServerSettingOperation,
  ServerSettingValue,
  errorMessage,
  isRegistrationLifetimeResult,
  submissionOperationId,
} from './device-api';
import { Icon } from './icon';

const SETTING_HELP: Partial<Record<number, readonly string[]>> = {
  1: [
    'Lifetime is the registration validity period, in seconds, for a LwM2M server. The client renews its registration with an Update before it expires.',
    'Shorter lifetimes generally mean more registration traffic and power use. Longer lifetimes allow a longer gap before registration expires. Lifetime is not a sensor-reporting interval or proof that the device is currently online.',
    "AVSystem's Anjay example uses a 60-second lifetime and shows Updates about every 30 seconds. The exact Update schedule depends on the client and transport settings.",
  ],
  6: [
    'Controls what happens to LwM2M Observe notifications while the client is offline or this server account is disabled.',
    'True queues notifications to report when communication resumes. False lets the client discard them or temporarily suspend observations. Queue capacity depends on the device, so enabling storage does not guarantee a complete history.',
  ],
  7: [
    'Selects the transport binding the LwM2M client uses to communicate with this server. For example, U denotes UDP.',
    'Both the device and server must support the selected transport. Supported codes and queue-mode behavior depend on the client and LwM2M version. Changing the binding can interrupt communication, so verify support before applying it.',
  ],
  14: [
    'Sets the wait, in seconds, before the client tries to register with this server after registration to the preceding server in the configured registration order completes.',
    'This applies until the first successful registration after bootstrapping. It does not delay routine registration updates or change how often the device sends telemetry.',
  ],
  17: [
    'Sets how many consecutive communication attempts can fail before the client considers one communication sequence failed.',
    'A higher count gives the device more chances to recover within the same sequence, but can use more power and delay declaring failure. The Communication retry timer controls the wait between attempts.',
  ],
  18: [
    'Sets the base wait in seconds between attempts within a communication sequence. The retry delay doubles each time: a 10-second base gives waits of 10, 20, and 40 seconds.',
    'Longer waits reduce retry traffic during an outage but slow recovery when the server becomes reachable again. The sequence delay timer controls the separate wait between groups of attempts.',
  ],
  19: [
    'Sets how long, in seconds, the device waits before starting a new communication sequence after the previous group of attempts fails. It can apply to registration or bootstrapping sequences.',
    'This is not the wait between individual attempts. The LwM2M definition uses the maximum value to mean that no new sequence should start.',
  ],
  20: [
    'Sets how many consecutive communication sequences can fail before the client considers the registration attempt failed.',
    'A sequence is a group of attempts governed by the Communication retry count and timer. Increasing this limit gives the device more chances to register across separate sequences, but delays the final failure decision.',
  ],
  23: [
    'Controls whether the device can send data to this server using the client-initiated LwM2M Send operation.',
    'True disables Send; false enables it on devices that implement it. It does not disable Observe notifications, server-initiated reads, or registration updates.',
  ],
};

const DEVICE_SETTING_HELP: Partial<Record<number, { summary: string; details: string }>> = {
  0: {
    summary: 'Identifies the manufacturer reported by the device.',
    details:
      'Use it to identify who made the device. It is not the editable name shown in the device inventory.',
  },
  1: {
    summary: 'Shows the model number reported by the device.',
    details:
      'Use it to identify the product model when checking compatibility or looking up support documentation.',
  },
  2: {
    summary: 'Shows the serial number reported by the device.',
    details:
      'Use it to distinguish individual units of the same model. It is separate from the endpoint name used for registration.',
  },
  3: {
    summary: 'Shows the firmware version reported by the device.',
    details:
      'Use it to check which firmware the device reports running. After an update, read the resource again to refresh the recorded version.',
  },
  4: {
    summary:
      'Restarts the device without erasing its configuration and may briefly interrupt communication.',
    details:
      'Reboot requires confirmation. Acknowledgement of the command does not mean the device is back online.',
  },
  6: {
    summary: 'Lists the power sources the device reports as available.',
    details:
      'Each instance identifies a source type, such as a battery or an external supply. This resource does not show the battery charge level or which source is currently supplying power.',
  },
};

@Component({
  selector: 'app-device-page',
  imports: [CommonModule, ReactiveFormsModule, RouterLink, Icon],
  templateUrl: './device-page.html',
  styleUrls: ['./app.css', './device-page.css'],
})
export class DevicePage {
  private readonly api = inject(DeviceApi);
  private readonly route = inject(ActivatedRoute);
  private readonly destroy = inject(DestroyRef);
  private generation = 0;
  private serverCheckTimer: ReturnType<typeof setTimeout> | null = null;
  private serverChecksRemaining = 0;
  private serverSettingsLoaded = false;
  readonly showingSettings = toSignal(
    this.route.queryParamMap.pipe(map((params) => params.get('view') === 'settings')),
    { initialValue: false },
  );
  readonly endpoint = signal('');
  readonly details = signal<DeviceDetails | null>(null);
  readonly lastSeen = signal<DeviceLastSeen | null>(null);
  readonly lastSeenLoading = signal(false);
  readonly lastSeenError = signal('');
  readonly loading = signal(false);
  readonly pageError = signal('');
  readonly lifetimeDraft = new FormControl<number | null>(null, {
    validators: [
      Validators.required,
      Validators.min(1),
      Validators.max(4294967295),
      Validators.pattern(/^[1-9]\d*$/),
    ],
  });
  readonly lifetimeResult = signal<RegistrationLifetimeResult | null>(null);
  readonly lifetimeProcessing = signal(false);
  readonly lifetimeError = signal('');
  readonly lifetimeStatus = signal('');
  readonly lifetimeWriteUnconfirmed = signal(false);
  readonly settingHelp = SETTING_HELP;
  readonly deviceSettingHelp = DEVICE_SETTING_HELP;
  readonly openSettingInfo = signal<number | null>(null);
  readonly serverSectionOpen = signal(true);
  readonly configurationGroup = signal<'server' | 'device'>('server');
  readonly deviceSettings = signal<DeviceSetting[]>([]);
  readonly deviceSettingsLoading = signal(false);
  readonly deviceSettingsError = signal('');
  readonly rebootConfirmation = signal(false);
  readonly serverAutoChecking = signal(false);
  readonly serverSettings = signal<ServerSetting[]>([]);
  readonly settingSelection = signal<{ resourceId: number; mode: 'details' | 'edit' }>({
    resourceId: 1,
    mode: 'details',
  });
  readonly settingRows = computed(() =>
    this.configurationGroup() === 'device'
      ? this.deviceSettings().map((setting) => ({ ...setting, writable: false }))
      : [
          { resource_id: 1, label: 'Registration lifetime', readable: true, writable: true },
          ...this.serverSettings(),
        ],
  );
  readonly selectedDeviceSetting = computed(() =>
    this.deviceSettings().find(
      (setting) => setting.resource_id === this.settingSelection().resourceId,
    ),
  );
  readonly selectedServerSettings = computed(() =>
    this.serverSettings().filter(
      (setting) => setting.resource_id === this.settingSelection().resourceId,
    ),
  );
  readonly serverSettingsProcessing = signal(false);
  readonly serverSettingsError = signal('');
  readonly serverOperation = signal<ServerSettingOperation | null>(null);
  readonly serverSubmissionUnknown = signal(false);
  readonly serverDrafts = new Map<number, FormControl<ServerSettingValue | null>>();

  constructor() {
    this.destroy.onDestroy(() => {
      ++this.generation;
      this.stopServerChecks();
    });
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      ++this.generation;
      this.stopServerChecks();
      this.endpoint.set(params.get('endpoint') ?? '');
      this.details.set(null);
      this.lastSeen.set(null);
      this.lastSeenLoading.set(false);
      this.lastSeenError.set('');
      this.loading.set(false);
      this.lifetimeDraft.reset();
      this.lifetimeResult.set(null);
      this.lifetimeProcessing.set(false);
      this.lifetimeError.set('');
      this.lifetimeStatus.set('');
      this.lifetimeWriteUnconfirmed.set(false);
      this.openSettingInfo.set(null);
      this.serverSectionOpen.set(true);
      this.configurationGroup.set('server');
      this.deviceSettings.set([]);
      this.deviceSettingsLoading.set(false);
      this.deviceSettingsError.set('');
      this.selectSetting(1, 'details');
      this.serverSettingsLoaded = false;
      this.serverSettings.set([]);
      this.serverSettingsProcessing.set(false);
      this.serverSettingsError.set('');
      this.serverOperation.set(null);
      this.serverSubmissionUnknown.set(false);
      this.serverDrafts.clear();
      if (!this.endpoint()) {
        this.pageError.set('Device endpoint is missing.');
        return;
      }
      void this.loadDetails();
    });
    effect(() => {
      const endpoint = this.endpoint();
      if (this.showingSettings() && endpoint) {
        untracked(() => {
          if (!this.serverSettingsLoaded) void this.loadServerSettings();
          void this.readLifetime();
        });
      } else {
        this.rebootConfirmation.set(false);
      }
    });
  }

  selectSetting(resourceId: number, mode: 'details' | 'edit'): void {
    this.openSettingInfo.set(null);
    this.rebootConfirmation.set(false);
    this.settingSelection.set({ resourceId, mode });
  }

  selectConfigurationGroup(group: 'server' | 'device'): void {
    if (this.configurationGroup() === group) {
      this.serverSectionOpen.update((open) => !open);
      this.rebootConfirmation.set(false);
    } else {
      this.configurationGroup.set(group);
      this.serverSectionOpen.set(true);
      this.selectSetting(group === 'server' ? 1 : 0, 'details');
    }
    if (group === 'device' && this.serverSectionOpen() && !this.deviceSettings().length) {
      void this.loadDeviceSettings();
    }
  }

  async loadDeviceSettings(): Promise<void> {
    if (this.deviceSettingsLoading()) return;
    const generation = this.generation;
    this.deviceSettingsLoading.set(true);
    this.deviceSettingsError.set('');
    try {
      const settings = await this.api.deviceSettings(this.endpoint());
      if (generation === this.generation) this.deviceSettings.set(settings);
    } catch (error) {
      if (generation === this.generation) this.deviceSettingsError.set(errorMessage(error));
    } finally {
      if (generation === this.generation) this.deviceSettingsLoading.set(false);
    }
  }

  reloadDevicePage(): void {
    void this.loadDetails();
    if (this.showingSettings()) void this.readLifetime();
  }

  async loadDetails(): Promise<void> {
    const generation = this.generation;
    this.loading.set(true);
    this.pageError.set('');
    try {
      const detail = await this.api.detail(this.endpoint());
      if (generation === this.generation) {
        this.details.set(detail);
        if (detail.local.coiote_device_id) void this.loadLastSeen();
      }
    } catch (error) {
      if (generation === this.generation) {
        this.details.set(null);
        this.pageError.set(errorMessage(error));
      }
    } finally {
      if (generation === this.generation) this.loading.set(false);
    }
  }

  private async loadLastSeen(): Promise<void> {
    if (this.lastSeenLoading()) return;
    const generation = this.generation;
    this.lastSeen.set(null);
    this.lastSeenError.set('');
    this.lastSeenLoading.set(true);
    try {
      const result = await this.api.lastSeen(this.endpoint());
      if (generation === this.generation) this.lastSeen.set(result);
    } catch {
      if (generation === this.generation) {
        this.lastSeenError.set('Unavailable. Use Reload details to try again.');
      }
    } finally {
      if (generation === this.generation) this.lastSeenLoading.set(false);
    }
  }

  serverSettingsBusy(): boolean {
    return (
      this.serverSettingsProcessing() ||
      this.serverSubmissionUnknown() ||
      ['pending', 'submission_unknown'].includes(this.serverOperation()?.state ?? '')
    );
  }

  async loadServerSettings(): Promise<void> {
    if (this.serverSettingsProcessing()) return;
    this.stopServerChecks();
    const generation = this.generation;
    this.serverSettingsProcessing.set(true);
    this.serverSettingsError.set('');
    try {
      const result = await this.api.serverSettings(this.endpoint());
      if (generation !== this.generation) return;
      this.serverSettingsLoaded = true;
      this.serverSettings.set(result.settings);
      const operation = result.unresolved_operation ?? result.completed_operation;
      if (operation) this.applyServerOperation(operation);
      for (const setting of result.settings) {
        if (!this.serverDrafts.has(setting.resource_id)) {
          this.serverDrafts.set(
            setting.resource_id,
            new FormControl<ServerSettingValue | null>(null),
          );
        }
      }
      this.startServerChecks();
    } catch (error) {
      if (generation === this.generation) this.serverSettingsError.set(errorMessage(error));
    } finally {
      if (generation === this.generation) this.serverSettingsProcessing.set(false);
    }
  }

  serverDraft(setting: ServerSetting): FormControl<ServerSettingValue | null> {
    const draft = this.serverDrafts.get(setting.resource_id);
    if (!draft) throw new Error('Server setting draft was not initialized.');
    return draft;
  }

  validServerDraft(setting: ServerSetting): boolean {
    const value = this.serverDraft(setting).value;
    if (setting.data_type === 'boolean') return typeof value === 'boolean';
    if (setting.data_type === 'integer') {
      return (
        typeof value === 'number' &&
        Number.isSafeInteger(value) &&
        value >= (setting.minimum ?? 0) &&
        value <= (setting.maximum ?? 4294967295)
      );
    }
    return (
      typeof value === 'string' &&
      /^(?:UQ|SQ|UQS|[UTSN]{1,4})$/.test(value) &&
      new Set(value).size === value.length
    );
  }

  async submitServerSetting(setting: ServerSetting, kind: 'read' | 'write'): Promise<void> {
    if (this.serverSettingsBusy()) return;
    const value = this.serverDraft(setting).value;
    if (
      !setting.readable ||
      (kind === 'write' && (value === null || !setting.writable || !this.validServerDraft(setting)))
    ) {
      this.serverSettingsError.set(
        'Select a supported setting and enter a valid value before applying.',
      );
      return;
    }
    if (kind === 'read') {
      await this.submitConfigurationOperation(setting.resource_id, kind, null, false, () =>
        this.api.readServerSetting(this.endpoint(), setting.resource_id),
      );
    } else if (value !== null) {
      await this.submitConfigurationOperation(setting.resource_id, kind, value, false, () =>
        this.api.writeServerSetting(this.endpoint(), setting.resource_id, value),
      );
    }
  }

  async submitDeviceSetting(setting: DeviceSetting): Promise<void> {
    if (this.serverSettingsBusy() || this.deviceSettingsLoading() || this.lifetimeProcessing())
      return;
    if (setting.resource_id === 4) {
      if (!setting.executable || !this.rebootConfirmation()) return;
      this.rebootConfirmation.set(false);
      await this.submitConfigurationOperation(4, 'reboot', null, true, () =>
        this.api.rebootDevice(this.endpoint()),
      );
    } else if (setting.readable) {
      await this.submitConfigurationOperation(setting.resource_id, 'read', null, true, () =>
        this.api.readDeviceSetting(this.endpoint(), setting.resource_id),
      );
    }
  }

  private async submitConfigurationOperation(
    resourceId: number,
    kind: ServerSettingOperation['kind'],
    value: ServerSettingValue | null,
    deviceResource: boolean,
    submit: () => Promise<ServerSettingOperation>,
  ): Promise<void> {
    const generation = this.generation;
    this.serverSettingsProcessing.set(true);
    this.serverSettingsError.set('');
    this.serverOperation.set(null);
    try {
      const operation = await submit();
      if (generation === this.generation) {
        if (
          operation.resource_id !== resourceId ||
          operation.kind !== kind ||
          operation.resource.startsWith('Device.0.') !== deviceResource ||
          (kind === 'write' && operation.requested_value !== value)
        ) {
          this.serverSubmissionUnknown.set(true);
          this.serverSettingsError.set(
            'The submission result does not match the requested setting. Refresh this page to recover the operation.',
          );
          return;
        }
        this.applyServerOperation(operation);
        this.startServerChecks();
      }
    } catch (error) {
      if (generation === this.generation) {
        const uncertain =
          !!submissionOperationId(error) ||
          !(error instanceof HttpErrorResponse) ||
          error.status === 0 ||
          error.status >= 500;
        this.serverSubmissionUnknown.set(uncertain);
        this.serverSettingsError.set(
          `${errorMessage(error)}${uncertain ? ' Refresh this page to recover any persisted operation. Do not blindly retry.' : ''}`,
        );
      }
    } finally {
      if (generation === this.generation) this.serverSettingsProcessing.set(false);
    }
  }

  async checkServerSetting(automatic = false): Promise<void> {
    const operation = this.serverOperation();
    if (!operation || this.serverSettingsProcessing()) return;
    if (!automatic) this.stopServerChecks();
    const generation = this.generation;
    this.serverSettingsProcessing.set(true);
    this.serverSettingsError.set('');
    try {
      const result = await this.api.serverSettingOperation(this.endpoint(), operation.id);
      if (generation === this.generation) {
        if (
          result.id !== operation.id ||
          result.resource_id !== operation.resource_id ||
          result.resource !== operation.resource ||
          result.kind !== operation.kind
        ) {
          this.serverSettingsError.set(
            'The returned operation does not match the requested setting.',
          );
          return;
        }
        this.applyServerOperation(result);
      }
    } catch (error) {
      if (generation === this.generation) this.serverSettingsError.set(errorMessage(error));
    } finally {
      if (generation === this.generation) {
        this.serverSettingsProcessing.set(false);
        if (!automatic) this.serverChecksRemaining = 30;
        this.scheduleServerCheck();
      }
    }
  }

  private stopServerChecks(): void {
    if (this.serverCheckTimer !== null) clearTimeout(this.serverCheckTimer);
    this.serverCheckTimer = null;
    this.serverChecksRemaining = 0;
    this.serverAutoChecking.set(false);
  }

  private startServerChecks(): void {
    this.stopServerChecks();
    this.serverChecksRemaining = 30;
    this.scheduleServerCheck();
  }

  private scheduleServerCheck(): void {
    if (
      this.serverChecksRemaining <= 0 ||
      this.serverOperation()?.state !== 'pending' ||
      this.serverSettingsError()
    ) {
      this.serverAutoChecking.set(false);
      return;
    }
    this.serverAutoChecking.set(true);
    this.serverCheckTimer = setTimeout(() => {
      this.serverCheckTimer = null;
      --this.serverChecksRemaining;
      void this.checkServerSetting(true);
    }, 2000);
  }

  private applyServerOperation(operation: ServerSettingOperation): void {
    if (operation.state !== 'pending') this.stopServerChecks();
    this.serverOperation.set(operation);
    this.serverSubmissionUnknown.set(false);
    if (operation.resource.startsWith('Device.0.')) {
      if (operation.state === 'succeeded' && operation.kind === 'read') {
        const observations = operation.observations;
        if (observations?.length) {
          this.deviceSettings.update((settings) =>
            settings.map((setting) =>
              setting.resource_id === operation.resource_id
                ? { ...setting, observations }
                : setting,
            ),
          );
        } else {
          this.serverSettingsError.set('The Device read completed without any observations.');
        }
      }
    } else if (operation.state === 'succeeded' || operation.state === 'verification_failed') {
      if (
        operation.value === null ||
        !operation.observed_at ||
        (operation.state === 'succeeded' &&
          operation.kind === 'write' &&
          operation.value !== operation.requested_value)
      ) {
        this.serverSubmissionUnknown.set(true);
        this.serverSettingsError.set(
          'The setting verification result is incomplete or mismatched. Do not retry.',
        );
        return;
      }
      this.serverSettings.update((settings) =>
        settings.map((setting) =>
          setting.resource_id === operation.resource_id
            ? { ...setting, value: operation.value, observed_at: operation.observed_at }
            : setting,
        ),
      );
    }
    if (operation.error) this.serverSettingsError.set(operation.error);
  }

  async readLifetime(): Promise<void> {
    if (this.lifetimeProcessing()) return;
    const generation = this.generation;
    this.lifetimeProcessing.set(true);
    this.lifetimeError.set('');
    this.lifetimeStatus.set('');
    try {
      const result = await this.api.readLifetime(this.endpoint());
      if (generation !== this.generation) return;
      if (
        result.operation !== 'read' ||
        result.state !== 'succeeded' ||
        !this.setLifetimeResult(result)
      ) {
        this.lifetimeError.set('The backend did not return a confirmed registration lifetime.');
      }
    } catch (error) {
      if (generation === this.generation) this.lifetimeError.set(errorMessage(error));
    } finally {
      if (generation === this.generation) this.lifetimeProcessing.set(false);
    }
  }

  async writeLifetime(): Promise<void> {
    if (this.lifetimeProcessing()) return;
    const lifetime = this.lifetimeDraft.value;
    if (
      lifetime === null ||
      !Number.isSafeInteger(lifetime) ||
      lifetime < 1 ||
      lifetime > 4294967295
    ) {
      this.lifetimeDraft.markAsTouched();
      this.lifetimeError.set('Enter a whole number from 1 to 4294967295 seconds.');
      return;
    }
    if (this.lifetimeWriteUnconfirmed()) {
      this.lifetimeError.set(
        'The previous write outcome is unconfirmed. Verify the device state before trying again.',
      );
      return;
    }

    const generation = this.generation;
    this.lifetimeProcessing.set(true);
    this.lifetimeError.set('');
    this.lifetimeStatus.set('');
    try {
      const result = await this.api.writeLifetime(this.endpoint(), lifetime);
      if (generation !== this.generation) return;
      if (
        result.state === 'verification_failed' &&
        result.requested_lifetime === lifetime &&
        this.setLifetimeResult(result)
      ) {
        this.lifetimeError.set(
          `The device reported ${result.lifetime} seconds; the requested ${lifetime} seconds was not verified.`,
        );
        return;
      }
      if (
        result.operation !== 'write' ||
        result.requested_lifetime !== lifetime ||
        result.matches_requested !== true ||
        result.lifetime !== lifetime ||
        !this.setLifetimeResult(result)
      ) {
        this.lifetimeWriteUnconfirmed.set(true);
        this.lifetimeError.set(
          'The backend did not confirm the requested lifetime. Verify the device state before trying again.',
        );
        return;
      }
      this.lifetimeStatus.set('Registration lifetime applied and verified on the device.');
    } catch (error) {
      if (generation === this.generation) {
        if (
          error instanceof HttpErrorResponse &&
          error.status === 409 &&
          isRegistrationLifetimeResult(error.error) &&
          error.error.state === 'verification_failed' &&
          error.error.requested_lifetime === lifetime &&
          this.setLifetimeResult(error.error)
        ) {
          this.lifetimeError.set(
            `The device reported ${error.error.lifetime} seconds; the requested ${lifetime} seconds was not verified.`,
          );
        } else {
          const uncertain =
            !(error instanceof HttpErrorResponse) ||
            error.status === 0 ||
            error.status === 409 ||
            error.status >= 500;
          this.lifetimeWriteUnconfirmed.set(uncertain);
          const message = errorMessage(error);
          this.lifetimeError.set(
            uncertain ? `${message} Do not retry until the device state is verified.` : message,
          );
        }
      }
    } finally {
      if (generation === this.generation) this.lifetimeProcessing.set(false);
    }
  }

  private setLifetimeResult(result: RegistrationLifetimeResult): boolean {
    if (
      (result.state !== 'succeeded' &&
        !(
          result.state === 'verification_failed' &&
          result.operation === 'write' &&
          result.matches_requested === false &&
          result.requested_lifetime !== result.lifetime
        )) ||
      !Number.isSafeInteger(result.lifetime) ||
      result.lifetime === null ||
      result.lifetime < 1 ||
      result.lifetime > 4294967295 ||
      !result.observed_at ||
      Number.isNaN(Date.parse(result.observed_at))
    ) {
      return false;
    }
    this.lifetimeResult.set(result);
    return true;
  }
}
