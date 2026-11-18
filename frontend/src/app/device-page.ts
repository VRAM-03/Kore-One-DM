import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { map } from 'rxjs';
import {
  DeviceApi,
  DeviceDetails,
  RegistrationLifetimeResult,
  ServerSetting,
  ServerSettingOperation,
  ServerSettingValue,
  errorMessage,
  isRegistrationLifetimeResult,
  submissionOperationId,
} from './device-api';
import { Icon } from './icon';

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
  readonly lifetimeInfoOpen = signal(false);
  readonly serverSectionOpen = signal(true);
  readonly serverAutoChecking = signal(false);
  readonly serverSettings = signal<ServerSetting[]>([]);
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
      this.loading.set(false);
      this.lifetimeDraft.reset();
      this.lifetimeResult.set(null);
      this.lifetimeProcessing.set(false);
      this.lifetimeError.set('');
      this.lifetimeStatus.set('');
      this.lifetimeWriteUnconfirmed.set(false);
      this.lifetimeInfoOpen.set(false);
      this.serverSectionOpen.set(true);
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
      if (this.showingSettings() && endpoint && !this.serverSettingsLoaded) {
        untracked(() => void this.loadServerSettings());
      }
    });
  }

  async loadDetails(): Promise<void> {
    const generation = this.generation;
    this.loading.set(true);
    this.pageError.set('');
    try {
      const detail = await this.api.detail(this.endpoint());
      if (generation === this.generation) this.details.set(detail);
    } catch (error) {
      if (generation === this.generation) {
        this.details.set(null);
        this.pageError.set(errorMessage(error));
      }
    } finally {
      if (generation === this.generation) this.loading.set(false);
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
    const generation = this.generation;
    this.serverSettingsProcessing.set(true);
    this.serverSettingsError.set('');
    this.serverOperation.set(null);
    try {
      let operation: ServerSettingOperation;
      if (kind === 'read') {
        operation = await this.api.readServerSetting(this.endpoint(), setting.resource_id);
      } else if (value !== null) {
        operation = await this.api.writeServerSetting(this.endpoint(), setting.resource_id, value);
      } else {
        throw new Error('A server setting value is required.');
      }
      if (generation === this.generation) {
        if (
          operation.resource_id !== setting.resource_id ||
          operation.kind !== kind ||
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
        if (result.id !== operation.id || result.resource_id !== operation.resource_id) {
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
    if (operation.state === 'succeeded' || operation.state === 'verification_failed') {
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
      if (!this.setLifetimeResult(result)) {
        this.lifetimeError.set('The backend did not return a confirmed registration lifetime.');
        return;
      }
      this.lifetimeStatus.set('Latest confirmed lifetime loaded.');
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
      if (result.state === 'verification_failed' && this.setLifetimeResult(result)) {
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
      result.state !== 'succeeded' ||
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
