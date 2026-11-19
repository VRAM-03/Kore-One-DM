import { CommonModule, DOCUMENT, Location } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  Component,
  DestroyRef,
  ElementRef,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { catchError, exhaustMap, filter, map, of, timer } from 'rxjs';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import Keycloak from 'keycloak-js';
import { SIGNED_OUT_PATH } from './auth';
import { CreateDevice, Device, DeviceActivity, DeviceApi, errorMessage } from './device-api';
import { Icon } from './icon';

type DialogMode = 'create' | 'rename' | 'delete' | 'configure';

@Component({
  imports: [CommonModule, ReactiveFormsModule, RouterOutlet, Icon],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  private readonly api = inject(DeviceApi);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly document = inject(DOCUMENT);
  private readonly keycloak = inject(Keycloak);
  private configurationFeedbackTimer: ReturnType<typeof setTimeout> | undefined;
  readonly configuredSuccessfully = signal(false);
  readonly signedIn = this.keycloak.authenticated === true;
  readonly email = [this.keycloak.idTokenParsed?.['email'], this.keycloak.tokenParsed?.['email']]
    .find((value): value is string => typeof value === 'string' && value.trim().length > 0);
  readonly loggingOut = signal(false);
  readonly devicePage = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.router.url.startsWith('/devices/')),
    ),
    { initialValue: this.location.path().startsWith('/devices/') },
  );
  @ViewChild('dialog') private dialog?: ElementRef<HTMLDialogElement>;
  readonly view = signal<'dashboard' | 'devices' | 'connector'>('devices');
  readonly mobileMenu = signal(false);
  readonly sidebarCollapsed = signal(false);
  readonly devices = signal<Device[]>([]);
  readonly activity = signal<DeviceActivity | null>(null);
  readonly activityError = signal(false);
  readonly loading = signal(false);
  readonly syncing = signal(false);
  readonly busy = computed(() => this.loading() || this.saving() || this.syncing());
  readonly loadError = signal('');
  readonly notice = signal('');
  readonly noticeError = signal(false);
  readonly query = signal('');
  readonly page = signal(0);
  readonly pageSize = signal(10);
  readonly sortAscending = signal(false);
  readonly health = signal<'checking' | 'available' | 'unavailable'>('checking');
  readonly modalMode = signal<DialogMode>('create');
  readonly selected = signal<Device | null>(null);
  readonly modalError = signal('');
  readonly saving = signal(false);
  readonly createBlocked = signal(false);
  readonly connectorForm = new FormGroup({
    username: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(254), Validators.pattern(/\S/)],
    }),
    password: new FormControl('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(1024)],
    }),
  });
  readonly form = new FormGroup({
    connector: new FormControl<'' | 'coiote'>('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    endpoint: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    name: new FormControl('', { nonNullable: true }),
    mode: new FormControl<'psk' | 'nosec'>('psk', { nonNullable: true }),
    identity: new FormControl('', { nonNullable: true }),
    key: new FormControl('', { nonNullable: true }),
  });
  readonly renameForm = new FormGroup({
    name: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });
  readonly deleteConfirm = new FormControl('', { nonNullable: true });
  readonly deleteForm = new FormGroup({ confirmation: this.deleteConfirm });
  readonly filtered = computed(() => {
    const query = this.query().trim().toLowerCase();
    const rows = this.devices().filter((d) =>
      `${d.endpoint_name} ${d.friendly_name ?? ''}`.toLowerCase().includes(query),
    );
    return [...rows].sort((a, b) => {
      if (!a.created_at) return b.created_at ? 1 : a.id - b.id;
      if (!b.created_at) return -1;
      const difference = Date.parse(a.created_at) - Date.parse(b.created_at);
      return (difference || a.id - b.id) * (this.sortAscending() ? 1 : -1);
    });
  });
  readonly pages = computed(() => Math.max(1, Math.ceil(this.filtered().length / this.pageSize())));
  readonly currentPage = computed(() => Math.min(this.page(), this.pages() - 1));
  readonly rows = computed(() =>
    this.filtered().slice(
      this.currentPage() * this.pageSize(),
      (this.currentPage() + 1) * this.pageSize(),
    ),
  );
  readonly rangeStart = computed(() =>
    this.filtered().length ? this.currentPage() * this.pageSize() + 1 : 0,
  );
  readonly rangeEnd = computed(() =>
    Math.min((this.currentPage() + 1) * this.pageSize(), this.filtered().length),
  );
  constructor() {
    inject(DestroyRef).onDestroy(() => clearTimeout(this.configurationFeedbackTimer));
    effect(() => {
      if (!this.signedIn) return;
      const onDevicePage = this.devicePage();
      untracked(() => {
        if (onDevicePage) void this.checkHealth();
        else void this.loadDevices();
      });
    });
    effect((onCleanup) => {
      if (!this.signedIn || this.view() !== 'dashboard' || this.devicePage()) return;
      this.activity.set(null);
      this.activityError.set(false);
      const subscription = timer(0, 30000)
        .pipe(exhaustMap(() => this.api.activity().pipe(catchError(() => of(null)))))
        .subscribe((result) => {
          this.activity.set(result);
          this.activityError.set(result === null);
        });
      onCleanup(() => subscription.unsubscribe());
    });
  }

  async logout(): Promise<void> {
    if (this.loggingOut()) return;
    this.loggingOut.set(true);
    try {
      const redirectUri = new URL(`/${SIGNED_OUT_PATH}`, this.document.baseURI).href;
      await this.keycloak.logout({ redirectUri });
    } catch {
      this.notify('Could not sign out. Please try again.', true);
    } finally {
      this.loggingOut.set(false);
    }
  }

  async checkHealth(): Promise<void> {
    this.health.set('checking');
    try {
      const result = await this.api.health();
      this.health.set(result.status === 'ok' ? 'available' : 'unavailable');
    } catch {
      this.health.set('unavailable');
    }
  }

  navigate(view: 'dashboard' | 'devices' | 'connector'): void {
    this.view.set(view);
    this.mobileMenu.set(false);
    if (this.devicePage()) {
      void this.router
        .navigate(['/'])
        .catch(() => this.notify('Could not open the workspace.', true));
    }
  }

  search(value: string): void {
    this.query.set(value);
    this.page.set(0);
  }
  setPageSize(value: string): void {
    this.pageSize.set(Number(value));
    this.page.set(0);
  }
  goPage(page: number): void {
    this.page.set(Math.max(0, Math.min(page, this.pages() - 1)));
  }

  async loadDevices(): Promise<boolean> {
    if (this.loading()) return false;
    this.loading.set(true);
    this.loadError.set('');
    void this.checkHealth();
    try {
      this.devices.set(await this.api.list());
      return true;
    } catch (error) {
      this.devices.set([]);
      this.loadError.set(errorMessage(error));
      return false;
    } finally {
      this.loading.set(false);
    }
  }

  async syncDevices(): Promise<void> {
    if (this.busy()) return;
    this.syncing.set(true);
    this.notice.set('');
    try {
      const result = await this.api.sync();
      try {
        const devices = await this.api.list();
        this.devices.set(devices);
        this.loadError.set('');
        this.page.set(0);
      } catch (error) {
        this.notify(
          `Sync completed, but the list could not be reloaded. ${errorMessage(error)}`,
          true,
        );
        return;
      }
      this.notify(
        `Sync complete: ${result.synced.length} imported, ${result.updated.length} updated, ` +
          `${result.missing_from_coiote.length} not found in the connector.`,
      );
    } catch (error) {
      this.notify(`Sync failed. ${errorMessage(error)}`, true);
    } finally {
      this.syncing.set(false);
    }
  }

  openCreate(): void {
    this.form.reset({ connector: '', endpoint: '', name: '', mode: 'psk', identity: '', key: '' });
    this.createBlocked.set(false);
    this.openDialog('create');
  }

  openConnectorConfiguration(): void {
    this.connectorForm.reset();
    this.openDialog('configure');
  }

  async saveConnectorConfiguration(): Promise<void> {
    if (this.saving() || this.connectorForm.invalid) return;
    const { username, password } = this.connectorForm.getRawValue();
    this.saving.set(true);
    this.modalError.set('');
    this.notice.set('');
    clearTimeout(this.configurationFeedbackTimer);
    this.configuredSuccessfully.set(false);
    this.connectorForm.reset();
    try {
      await this.api.saveCoioteConfiguration(username.trim(), password);
      this.dialog?.nativeElement.close();
      this.configuredSuccessfully.set(true);
      this.configurationFeedbackTimer = setTimeout(() => {
        this.configuredSuccessfully.set(false);
      }, 12000);
    } catch (error) {
      this.modalError.set(
        error instanceof HttpErrorResponse && error.status === 403
          ? 'Open the local console to save this configuration.'
          : 'Could not confirm the save. Re-enter the credentials to try again.',
      );
    } finally {
      this.saving.set(false);
    }
  }

  onConnectorChanged(): void {
    this.form.controls.identity.reset('');
    this.form.controls.key.reset('');
    this.form.controls.mode.reset('psk');
    if (!this.createBlocked()) this.modalError.set('');
  }

  async openDetails(device: Device): Promise<void> {
    try {
      await this.router.navigate(['/devices', device.endpoint_name]);
    } catch (error) {
      this.notify(errorMessage(error), true);
    }
  }

  openRename(device: Device): void {
    this.selected.set(device);
    this.renameForm.reset({ name: device.friendly_name ?? '' });
    this.openDialog('rename');
  }

  openDelete(device: Device): void {
    this.selected.set(device);
    this.deleteConfirm.reset('');
    this.openDialog('delete');
  }

  private openDialog(mode: DialogMode): void {
    this.modalMode.set(mode);
    this.modalError.set('');
    if (!this.dialog?.nativeElement.open) this.dialog?.nativeElement.showModal();
  }

  closeDialog(): void {
    if (!this.saving()) this.dialog?.nativeElement.close();
  }

  onDialogClosed(): void {
    this.form.controls.identity.reset('');
    this.form.controls.key.reset('');
    this.connectorForm.reset();
    this.modalError.set('');
  }

  onCancel(event: Event): void {
    if (this.saving()) event.preventDefault();
  }

  async createDevice(): Promise<void> {
    if (this.saving() || this.createBlocked()) return;
    const fields = this.form.getRawValue();
    if (fields.connector !== 'coiote') {
      this.modalError.set('Select a connector type.');
      return;
    }
    if (!fields.endpoint.trim()) {
      this.modalError.set('Enter a nonblank endpoint name.');
      return;
    }
    if (
      fields.mode === 'psk' &&
      (!fields.identity.trim() || !/^(?:[0-9a-fA-F]{2})+$/.test(fields.key))
    ) {
      this.modalError.set(
        'PSK needs a nonblank identity and an even-length hexadecimal key. No spaces or 0x prefix. Credentials are not trimmed.',
      );
      return;
    }
    const body: CreateDevice = {
      endpoint_name: fields.endpoint.trim(),
      security_mode: fields.mode,
    };
    if (fields.name.trim()) body.friendly_name = fields.name.trim();
    if (fields.mode === 'psk') {
      body.psk_identity = fields.identity;
      body.psk_key = fields.key;
    }
    this.saving.set(true);
    this.modalError.set('');
    try {
      const device = await this.api.create(body);
      this.upsert(device);
      this.search('');
      this.navigate('devices');
      this.dialog?.nativeElement.close();
      this.notify(`Device "${device.endpoint_name}" created.`);
    } catch (error) {
      this.modalError.set(errorMessage(error));
      if (!(error instanceof HttpErrorResponse) || error.status === 0 || error.status >= 500) {
        this.createBlocked.set(true);
        this.form.controls.identity.reset('');
        this.form.controls.key.reset('');
      }
    } finally {
      this.saving.set(false);
    }
  }

  async renameDevice(): Promise<void> {
    const device = this.selected();
    const name = this.renameForm.controls.name.value.trim();
    if (!device || this.saving()) return;
    if (!name) {
      this.modalError.set('Enter a nonblank friendly name.');
      return;
    }
    this.saving.set(true);
    this.modalError.set('');
    try {
      this.upsert(await this.api.rename(device.endpoint_name, name));
      this.dialog?.nativeElement.close();
      this.notify('Friendly name updated.');
    } catch (error) {
      this.modalError.set(errorMessage(error));
    } finally {
      this.saving.set(false);
    }
  }

  async deleteDevice(): Promise<void> {
    const device = this.selected();
    if (!device || this.saving() || this.deleteConfirm.value !== device.endpoint_name) return;
    this.saving.set(true);
    this.modalError.set('');
    try {
      await this.api.delete(device.endpoint_name);
      this.devices.update((rows) => rows.filter((row) => row.id !== device.id));
      this.dialog?.nativeElement.close();
      this.notify(`Device "${device.endpoint_name}" deleted.`);
    } catch (error) {
      this.modalError.set(errorMessage(error));
    } finally {
      this.saving.set(false);
    }
  }

  private upsert(device: Device): void {
    this.devices.update((rows) => [...rows.filter((row) => row.id !== device.id), device]);
  }

  private notify(message: string, isError = false): void {
    this.noticeError.set(isError);
    this.notice.set(message);
  }
}
