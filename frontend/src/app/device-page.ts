import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  DeviceApi,
  DeviceDetails,
  DeviceOperation,
  DeviceResource,
  ResourceKey,
  errorMessage,
  submissionOperationId,
} from './device-api';
import { Icon } from './icon';

interface Reading {
  value: string;
  completed_at: string;
}

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
  readonly endpoint = signal('');
  readonly details = signal<DeviceDetails | null>(null);
  readonly loading = signal(false);
  readonly pageError = signal('');
  readonly resources = signal<DeviceResource[]>([]);
  readonly resourceLoading = signal(false);
  readonly resourceError = signal('');
  readonly readings = signal<Partial<Record<ResourceKey, Reading>>>({});
  readonly operations = signal<Partial<Record<ResourceKey, DeviceOperation>>>({});
  readonly operationIds = signal<Partial<Record<ResourceKey, string>>>({});
  readonly errors = signal<Partial<Record<ResourceKey, string>>>({});
  readonly processing = signal<Partial<Record<ResourceKey, boolean>>>({});
  readonly unconfirmed = signal<Partial<Record<ResourceKey, boolean>>>({});
  readonly timezoneKey: ResourceKey = 'Device.0.Timezone';
  readonly timezoneForm = new FormGroup({
    timezone: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });
  readonly lifetimeDraft = new FormControl<number | null>(null);
  readonly lifetimeInfoOpen = signal(false);

  constructor() {
    this.destroy.onDestroy(() => ++this.generation);
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      ++this.generation;
      this.endpoint.set(params.get('endpoint') ?? '');
      this.details.set(null);
      this.loading.set(false);
      this.resourceLoading.set(false);
      this.resources.set([]);
      this.readings.set({});
      this.operations.set({});
      this.operationIds.set({});
      this.errors.set({});
      this.processing.set({});
      this.unconfirmed.set({});
      this.timezoneForm.reset({ timezone: '' });
      this.lifetimeDraft.reset();
      this.lifetimeInfoOpen.set(false);
      if (!this.endpoint()) {
        this.pageError.set('Device endpoint is missing.');
        return;
      }
      void this.loadDetails();
      void this.loadResources();
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

  async loadResources(): Promise<void> {
    if (this.resourceLoading()) return;
    const generation = this.generation;
    this.resourceLoading.set(true);
    this.resourceError.set('');
    try {
      const result = await this.api.resources(this.endpoint());
      if (generation === this.generation) this.resources.set(result.resources);
    } catch (error) {
      if (generation === this.generation) {
        this.resources.set([]);
        this.resourceError.set(errorMessage(error));
      }
    } finally {
      if (generation === this.generation) this.resourceLoading.set(false);
    }
  }

  supports(key: ResourceKey, operation: 'Read' | 'Write'): boolean {
    return this.resources().some(
      (resource) => resource.key === key && resource.operations.includes(operation),
    );
  }

  unresolved(key: ResourceKey): boolean {
    const operation = this.operations()[key];
    return (
      !!this.unconfirmed()[key] ||
      !!(operation && !['succeeded', 'failed', 'rejected', 'not_found'].includes(operation.status))
    );
  }

  busy(key: ResourceKey): boolean {
    return !!this.processing()[key] || this.unresolved(key);
  }

  async readResource(key: ResourceKey): Promise<void> {
    if (this.busy(key)) return;
    if (!this.supports(key, 'Read')) {
      this.setError(key, 'Resource is not exposed as readable by this device.');
      return;
    }
    await this.submit(key);
  }

  async writeTimezone(): Promise<void> {
    const key = this.timezoneKey;
    if (this.busy(key)) return;
    const value = this.timezoneForm.controls.timezone.value.trim();
    if (!this.supports(key, 'Write') || !this.supports(key, 'Read') || !this.readings()[key]) {
      this.setError(key, 'Read the writable timezone from the device before applying a change.');
      return;
    }
    if (!value) {
      this.setError(key, 'Enter an IANA timezone, such as Etc/UTC.');
      return;
    }
    if (value === this.readings()[key]?.value) {
      this.setError(key, 'Timezone already matches the last verified value.');
      return;
    }
    await this.submit(key, value);
  }

  private async submit(key: ResourceKey, value?: string): Promise<void> {
    const generation = this.generation;
    this.processing.update((state) => ({ ...state, [key]: true }));
    this.setError(key, '');
    this.operationIds.update((state) => ({ ...state, [key]: undefined }));
    this.operations.update((state) => ({ ...state, [key]: undefined }));
    try {
      const operation =
        value === undefined
          ? await this.api.read(this.endpoint(), key)
          : await this.api.writeTimezone(this.endpoint(), value);
      if (generation === this.generation) this.applyOperation(key, operation);
    } catch (error) {
      if (generation === this.generation) {
        const id = submissionOperationId(error);
        if (id) this.operationIds.update((state) => ({ ...state, [key]: id }));
        const uncertain =
          !!id ||
          !(error instanceof HttpErrorResponse) ||
          error.status === 0 ||
          error.status >= 500;
        this.unconfirmed.update((state) => ({ ...state, [key]: uncertain }));
        this.setError(key, errorMessage(error));
      }
    } finally {
      if (generation === this.generation) {
        this.processing.update((state) => ({ ...state, [key]: false }));
      }
    }
  }

  async checkResult(key: ResourceKey): Promise<void> {
    const id = this.operationIds()[key];
    if (this.processing()[key]) return;
    if (!id) {
      this.setError(key, 'No confirmed operation ID is available. Do not blindly resubmit.');
      return;
    }
    const generation = this.generation;
    this.processing.update((state) => ({ ...state, [key]: true }));
    this.setError(key, '');
    try {
      const operation = await this.api.operation(this.endpoint(), id);
      if (generation === this.generation) this.applyOperation(key, operation, id);
    } catch (error) {
      if (generation === this.generation) this.setError(key, errorMessage(error));
    } finally {
      if (generation === this.generation) {
        this.processing.update((state) => ({ ...state, [key]: false }));
      }
    }
  }

  private applyOperation(key: ResourceKey, operation: DeviceOperation, expectedId?: string): void {
    if (operation.resource_key !== key || (expectedId && operation.id !== expectedId)) {
      this.unconfirmed.update((state) => ({ ...state, [key]: true }));
      this.setError(key, 'The operation result does not match the requested resource.');
      return;
    }
    const value = operation.value;
    const completedAt = operation.completed_at;
    if (
      operation.status === 'succeeded' &&
      (value === null ||
        !completedAt ||
        (operation.kind === 'write' && value !== operation.requested_value))
    ) {
      this.unconfirmed.update((state) => ({ ...state, [key]: true }));
      this.setError(key, 'The device verification result is incomplete or mismatched.');
      return;
    }
    this.operationIds.update((state) => ({ ...state, [key]: operation.id }));
    this.operations.update((state) => ({ ...state, [key]: operation }));
    this.unconfirmed.update((state) => ({ ...state, [key]: false }));
    if (operation.status === 'succeeded' && value !== null && completedAt) {
      this.readings.update((state) => ({
        ...state,
        [key]: { value, completed_at: completedAt },
      }));
      if (
        key === this.timezoneKey &&
        (operation.kind === 'write' || !this.timezoneForm.controls.timezone.dirty)
      ) {
        this.timezoneForm.reset({ timezone: value });
      }
    }
  }

  private setError(key: ResourceKey, message: string): void {
    this.errors.update((state) => ({ ...state, [key]: message }));
  }

  operationLabel(key: ResourceKey): string {
    if (this.processing()[key]) return 'Checking…';
    if (this.unconfirmed()[key]) return 'Submission unconfirmed — do not resubmit';
    const operation = this.operations()[key];
    if (!operation) return 'Not read';
    if (operation.status === 'succeeded') {
      return operation.kind === 'write' ? 'Applied and verified' : 'Read completed';
    }
    return operation.status.replaceAll('_', ' ');
  }
}
