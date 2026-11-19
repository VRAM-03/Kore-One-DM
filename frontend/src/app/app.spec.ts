import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { Router, provideRouter } from '@angular/router';
import Keycloak from 'keycloak-js';
import { App } from './app';
import { Device, DeviceApi, DeviceDetails, RegistrationLifetimeResult } from './device-api';
import { DevicePage } from './device-page';

const device: Device = {
  id: 1,
  endpoint_name: 'lab-device',
  friendly_name: 'Lab sensor',
  security_mode: 'psk',
  coiote_device_id: 'remote-1',
  onboarding_status: 'coiote_record_created',
  connection_status: 'unknown',
  refreshed_at: '2026-11-16T00:00:00Z',
  first_registered_at: '2026-11-15T00:00:00Z',
  last_registered_at: '2026-11-16T00:00:00Z',
  last_seen_at: '2026-11-16T00:00:00Z',
  created_at: '2026-11-14T00:00:00Z',
};
const detail: DeviceDetails = { local: device, coiote_snapshot: null };

describe('Device workspace', () => {
  function setup(authenticated = true, email: string | undefined = 'operator@example.test') {
    const keycloak = {
      authenticated,
      idTokenParsed: { email },
      logout: vi.fn().mockResolvedValue(undefined),
    };
    const api = {
      list: vi.fn().mockResolvedValue([device]),
      sync: vi.fn().mockResolvedValue({
        synced: ['new-device'],
        updated: ['lab-device'],
        missing_from_coiote: [],
      }),
      health: vi.fn().mockResolvedValue({ status: 'ok' }),
      lastSeen: vi.fn().mockResolvedValue({ last_seen_at: '2026-11-17T00:00:00Z' }),
      validateCoioteCredentials: vi.fn().mockResolvedValue({ valid: true }),
      saveCoioteConfiguration: vi.fn().mockResolvedValue(undefined),
      detail: vi.fn().mockResolvedValue(detail),
      create: vi.fn().mockResolvedValue({ ...device, id: 2, endpoint_name: 'new-device' }),
      rename: vi.fn().mockResolvedValue({ ...device, friendly_name: 'Updated' }),
      delete: vi.fn().mockResolvedValue(undefined),
      serverSettings: vi.fn().mockResolvedValue({ settings: [], unresolved_operation: null }),
      readLifetime: vi.fn().mockResolvedValue({
        task_id: 'lifetime-1',
        device_id: 'remote-1',
        resource: 'LwM2M Server.0.Lifetime',
        operation: 'read',
        state: 'succeeded',
        requested_lifetime: null,
        lifetime: 3600,
        matches_requested: null,
        observed_at: '2026-11-16T00:00:00Z',
      } satisfies RegistrationLifetimeResult),
    };
    TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([
          { path: 'devices/:endpoint', component: DevicePage },
          { path: '', children: [] },
        ]),
        { provide: DeviceApi, useValue: api },
        { provide: Keycloak, useValue: keycloak },
      ],
    });
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    const dialog = element.querySelector('dialog');
    if (!dialog) return { api, fixture, app: fixture.componentInstance, element, keycloak };
    dialog.showModal = () => dialog.setAttribute('open', '');
    dialog.close = () => {
      dialog.removeAttribute('open');
      dialog.dispatchEvent(new Event('close'));
    };
    return { api, fixture, app: fixture.componentInstance, element, keycloak };
  }

  it('shows a single success toast below Configure after saving, without persisting on refresh', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    app.navigate('connector');
    await fixture.whenStable();
    const buttons = Array.from(
      element.querySelectorAll<HTMLButtonElement>('.connector-actions button'),
    );
    expect(buttons.map((button) => button.textContent?.trim())).toEqual([
      'Configure',
      'Check connection',
    ]);
    const configure = buttons[0];
    expect(configure.disabled).toBe(true);
    app.openConnectorConfiguration();
    await fixture.whenStable();
    const dialog = element.querySelector('dialog')!;
    const password = dialog.querySelector<HTMLInputElement>('[formControlName="password"]')!;
    expect(dialog.open).toBe(true);
    expect(password.type).toBe('password');
    app.connectorForm.setValue({ username: ' test-account ', password: ' test-password ' });
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    expect(api.validateCoioteCredentials).toHaveBeenCalledExactlyOnceWith(
      'test-account',
      ' test-password ',
    );
    expect(api.saveCoioteConfiguration).toHaveBeenCalledExactlyOnceWith(
      'test-account',
      ' test-password ',
    );
    expect(dialog.open).toBe(false);
    expect(app.connectorForm.getRawValue()).toEqual({ username: '', password: '' });
    expect(element.querySelector('.configuration-toast[role="status"]')?.textContent).toContain(
      'Configured Successfully',
    );
    expect(configure.nextElementSibling).toBe(element.querySelector('.configuration-toast'));
    expect(configure.nextElementSibling?.textContent?.trim()).toBe(
      'Configured Successfully',
    );
    expect(element.querySelectorAll('.configuration-toast')).toHaveLength(1);
    expect(element.querySelector('.configuration-toast app-icon')?.getAttribute('name')).toBe('check');
    expect(element.querySelector('.notification')).toBeNull();

    fixture.destroy();
    TestBed.resetTestingModule();
    const refreshed = setup();
    await refreshed.fixture.whenStable();
    refreshed.app.navigate('connector');
    refreshed.fixture.detectChanges();
    expect(refreshed.element.querySelector('.configuration-toast')).toBeNull();
  });

  it('expires configuration feedback after 12 seconds and restarts the timer on another save', async () => {
    const { fixture, app, element } = setup();
    await fixture.whenStable();
    app.navigate('connector');
    fixture.detectChanges();
    vi.useFakeTimers();
    try {
      app.openConnectorConfiguration();
      app.connectorForm.setValue({ username: 'test-account', password: 'test-password' });
      await app.saveConnectorConfiguration();
      await vi.advanceTimersByTimeAsync(6000);
      app.openConnectorConfiguration();
      app.connectorForm.setValue({ username: 'test-account', password: 'test-password' });
      await app.saveConnectorConfiguration();
      await vi.advanceTimersByTimeAsync(11999);
      fixture.detectChanges();
      expect(element.querySelector('.configuration-toast')?.textContent?.trim()).toBe(
        'Configured Successfully',
      );
      expect(element.querySelector('.configuration-toast')).not.toBeNull();
      await vi.advanceTimersByTimeAsync(1);
      fixture.detectChanges();
      expect(element.querySelector('.configuration-toast')).toBeNull();
    } finally {
      fixture.destroy();
      vi.useRealTimers();
    }
  });

  it('does not show configuration success feedback when the save fails', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    app.navigate('connector');
    app.openConnectorConfiguration();
    app.connectorForm.setValue({ username: 'test-account', password: 'test-password' });
    api.saveCoioteConfiguration.mockRejectedValueOnce(new HttpErrorResponse({ status: 500 }));
    await app.saveConnectorConfiguration();
    fixture.detectChanges();
    expect(element.querySelector('dialog')?.open).toBe(true);
    expect(element.querySelector('.form-error')?.textContent).toContain('Could not confirm the save');
    expect(element.querySelector('.configuration-toast')).toBeNull();
  });

  it('shows onboarding status, with neutral labels, and no timing or last seen columns', async () => {
    const { fixture, app, element } = setup();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(element.textContent).toContain('lab-device');
    const cells = element.querySelectorAll('tbody tr:first-child td');
    expect(cells[3]?.textContent).toContain('Created in KORE One DM');
    expect(element.querySelector('thead')?.textContent).toContain('Onboarding status');
    expect(element.querySelector('thead')?.textContent).not.toContain('Onboarding time');
    expect(element.querySelector('thead')?.textContent).not.toContain('Last seen');
    expect(cells[3]?.textContent).not.toContain('Nov 15, 2026');
    expect(element.textContent).not.toContain('Reachability unknown');
    expect(app.health()).toBe('available');
    expect(element.querySelector('header .backend-state')).toBeNull();
    expect(element.textContent).not.toContain('Online devices');
  });

  it('shows inventory-derived dashboard cards without the device table', async () => {
    const { fixture, app, element } = setup();
    await fixture.whenStable();
    const day = 24 * 60 * 60 * 1000;
    const ago = (days: number) => new Date(Date.now() - days * day).toISOString();
    app.devices.set([
      { ...device, id: 1, created_at: ago(2) },
      { ...device, id: 2, created_at: ago(6), connection_status: 'not_in_coiote' },
      { ...device, id: 3, created_at: ago(30), coiote_device_id: null,
        onboarding_status: 'coiote_registration_failed' },
      { ...device, id: 4, created_at: null, coiote_device_id: null, onboarding_status: 'created' },
    ]);
    app.navigate('dashboard');
    fixture.detectChanges();

    const cards = Array.from(element.querySelectorAll('.stat-card'));
    expect(
      cards.map((card) => [
        card.querySelector('span:not(.stat-icon)')?.textContent,
        card.querySelector('strong')?.textContent?.trim(),
      ]),
    ).toEqual([
      ['Total devices', '4'],
      ['New devices · last 7 days', '2'],
      ['Connector-linked devices', '2'],
      ['Provisioning failures', '1'],
    ]);
    expect(element.textContent).not.toContain('Recently active');
    expect(element.textContent).not.toContain('Needs attention');
    expect(element.querySelector('.inventory')).toBeNull();
    expect(element.querySelector('table')).toBeNull();

    app.navigate('devices');
    fixture.detectChanges();
    expect(element.querySelector('.inventory')).not.toBeNull();
    expect(element.querySelector('.stat-card')).toBeNull();
  });

  it('keeps retry available when the dashboard counts cannot be loaded', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    api.list.mockRejectedValueOnce(new Error('Inventory unavailable'));
    await app.loadDevices();
    app.navigate('dashboard');
    fixture.detectChanges();
    expect(Array.from(element.querySelectorAll('.stat-card strong')).map((v) => v.textContent?.trim()))
      .toEqual(['—', '—', '—', '—']);
    expect(element.querySelector('[role="alert"]')?.textContent).toContain('Inventory couldn');
    api.list.mockResolvedValueOnce([device]);
    element.querySelector<HTMLButtonElement>('[role="alert"] button')!.click();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(element.querySelector('.stat-card strong')?.textContent).toBe('1');
    });
  });

  it('shows the signed-in email and logs out through Keycloak', async () => {
    const { fixture, element, keycloak } = setup();
    await fixture.whenStable();
    expect(element.querySelector('.session-email')?.textContent).toContain('operator@example.test');
    element.querySelector<HTMLButtonElement>('.session-account button')!.click();
    expect(keycloak.logout).toHaveBeenCalledExactlyOnceWith({
      redirectUri: new URL('/signed-out', document.baseURI).href,
    });
  });

  it('shows a sign-in link without loading inventory when signed out', async () => {
    const { fixture, element, api } = setup(false);
    await fixture.whenStable();
    expect(element.querySelector('a')?.getAttribute('href')).toBe('/');
    expect(element.textContent).toContain('You are not signed in');
    expect(api.list).not.toHaveBeenCalled();
    expect(api.health).not.toHaveBeenCalled();
  });

  it('presents connector-neutral inventory and a conceptual super-admin workspace', async () => {
    const { fixture, app, element } = setup();
    await fixture.whenStable();
    app.devices.set([
      device,
      {
        ...device,
        id: 2,
        endpoint_name: 'imported-device',
        onboarding_status: 'synced_from_coiote',
      },
      {
        ...device,
        id: 3,
        endpoint_name: 'missing-device',
        connection_status: 'not_in_coiote',
        first_registered_at: null,
        last_seen_at: null,
      },
    ]);
    fixture.detectChanges();
    const inventory = element.querySelector('.inventory')!;
    expect(inventory.querySelector('thead')?.textContent).toContain('Device name');
    expect(inventory.querySelector('thead')?.textContent).not.toContain('Endpoint name');
    expect(inventory.textContent).not.toContain('Coiote');
    expect(inventory.textContent).not.toContain('Record created');
    expect(inventory.textContent).not.toContain('Connection history');
    expect(inventory.textContent).toContain('Imported from connector');
    expect(app.onboardingLabel(null)).toBe('—');
    expect(app.onboardingLabel('coiote_registration_failed')).toBe('Registration failed');
    expect(app.onboardingLabel('other_state')).toBe('other state');
    expect(inventory.textContent).not.toContain('No contact recorded');
    expect(inventory.querySelector('tbody')?.textContent).toContain(device.endpoint_name);
    const sort = inventory.querySelector<HTMLButtonElement>('.sort-button')!;
    expect(sort.getAttribute('aria-label')).toBe('Show oldest devices first');
    sort.click();
    fixture.detectChanges();
    expect(sort.getAttribute('aria-label')).toBe('Show newest devices first');
    const brand = element.querySelector('.brand')!;
    expect(brand.textContent?.trim()).toBe('One');
    expect(brand.querySelector('img')?.getAttribute('src')).toBe('kore-o.png');
    const role = element.querySelector('.role-banner')!;
    expect(role.textContent?.trim()).toBe('Super Admin');
    expect(role.previousElementSibling?.classList.contains('account-title')).toBe(true);
    expect(role.getAttribute('title')).toContain(
      'does not reflect account permissions',
    );
  });

  it('sorts by creation time in both directions with equal dates and missing dates handled consistently', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    app.devices.set([
      { ...device, id: 8, endpoint_name: 'a-older', created_at: '2026-11-15T12:00:00+02:00' },
      { ...device, id: 2, endpoint_name: 'z-newer', created_at: '2026-11-15T11:00:00Z' },
      { ...device, id: 3, endpoint_name: 'b-same-date', created_at: '2026-11-15T11:00:00Z' },
      { ...device, id: 1, endpoint_name: 'missing-date', created_at: null },
    ]);
    fixture.detectChanges();
    expect(app.rows().map((row) => row.id)).toEqual([3, 2, 8, 1]);
    const sort = element.querySelector<HTMLButtonElement>('.sort-button')!;
    expect(sort.closest('th')?.getAttribute('aria-sort')).toBe('descending');
    sort.click();
    fixture.detectChanges();
    expect(app.rows().map((row) => row.id)).toEqual([8, 2, 3, 1]);
    expect(sort.closest('th')?.getAttribute('aria-sort')).toBe('ascending');
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it('collapses and expands the sidebar without changing inventory or mobile navigation state', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    const toggle = element.querySelector<HTMLButtonElement>('.sidebar-toggle')!;
    expect(toggle.getAttribute('aria-label')).toBe('Collapse sidebar');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-controls')).toBe('device-sidebar');
    toggle.click();
    fixture.detectChanges();
    expect(element.querySelector('.shell')?.classList.contains('sidebar-collapsed')).toBe(true);
    expect(toggle.getAttribute('aria-label')).toBe('Expand sidebar');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    const devices = element.querySelector<HTMLButtonElement>('.nav-item[aria-label="Devices"]')!;
    expect(devices.title).toBe('Devices');
    const mobileToggle = element.querySelector<HTMLButtonElement>('.mobile-toggle')!;
    mobileToggle.click();
    fixture.detectChanges();
    expect(app.mobileMenu()).toBe(true);
    expect(app.sidebarCollapsed()).toBe(true);
    devices.click();
    fixture.detectChanges();
    expect(app.mobileMenu()).toBe(false);
    expect(app.view()).toBe('devices');
    expect(app.sidebarCollapsed()).toBe(true);
    toggle.click();
    fixture.detectChanges();
    expect(element.querySelector('.shell')?.classList.contains('sidebar-collapsed')).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it('searches locally and clamps pagination after filtering', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.devices.set(
      Array.from({ length: 12 }, (_, i) => ({
        ...device,
        id: i + 1,
        endpoint_name: `sensor-${i}`,
      })),
    );
    app.goPage(1);
    expect(app.rows()).toHaveLength(2);
    app.search('SENSOR-11');
    expect(app.rows()).toHaveLength(1);
    expect(app.currentPage()).toBe(0);
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it('rechecks the backend connection when refreshing after an outage', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    const disconnected = new HttpErrorResponse({ status: 0 });
    api.list.mockRejectedValue(disconnected);
    api.health.mockRejectedValue(disconnected);
    await app.loadDevices();
    app.navigate('connector');
    await fixture.whenStable();
    fixture.detectChanges();
    expect(element.querySelector('.backend-state')?.textContent).toContain(
      'Platform API unavailable',
    );
    api.list.mockResolvedValue([device]);
    api.health.mockResolvedValue({ status: 'ok' });
    await app.loadDevices();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(element.querySelector('.backend-state')?.textContent).toContain(
      'Platform API available',
    );
    app.navigate('devices');
    fixture.detectChanges();
    expect(element.querySelector('tbody')?.textContent).toContain('lab-device');
  });

  it('does not show old records as successful data after refresh fails', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    api.list.mockRejectedValue(
      new HttpErrorResponse({ status: 502, error: { detail: 'Coiote request failed' } }),
    );
    await app.loadDevices();
    expect(app.devices()).toEqual([]);
    expect(app.loadError()).toBe('Coiote request failed');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain("Inventory couldn't be loaded");
  });

  it('requires a connector before creating and reveals Coiote fields after selection', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    app.openCreate();
    fixture.detectChanges();
    expect(element.querySelector('select[formControlName="mode"]')).toBeNull();
    expect(element.querySelector('input[formControlName="key"]')).toBeNull();
    app.form.patchValue({ endpoint: 'new-device', identity: 'identity', key: 'AABB' });
    await app.createDevice();
    expect(api.create).not.toHaveBeenCalled();
    expect(app.modalError()).toBe('Select a connector type.');
    const connector = element.querySelector<HTMLSelectElement>(
      'select[formControlName="connector"]',
    )!;
    connector.value = 'coiote';
    connector.dispatchEvent(new Event('change', { bubbles: true }));
    fixture.detectChanges();
    expect(element.querySelector('select[formControlName="mode"]')).not.toBeNull();
    expect(element.querySelector<HTMLInputElement>('input[formControlName="key"]')?.type).toBe(
      'password',
    );
    expect(app.modalError()).toBe('');
  });

  it('clears hidden credentials when deselecting the connector and resets selection on reopen', async () => {
    const { fixture, app, element } = setup();
    await fixture.whenStable();
    app.openCreate();
    fixture.detectChanges();
    const connector = element.querySelector<HTMLSelectElement>(
      'select[formControlName="connector"]',
    )!;
    connector.value = 'coiote';
    connector.dispatchEvent(new Event('change', { bubbles: true }));
    app.form.patchValue({ identity: 'identity', key: 'AABB' });
    connector.value = '';
    connector.dispatchEvent(new Event('change', { bubbles: true }));
    fixture.detectChanges();
    expect(app.form.controls.identity.value).toBe('');
    expect(app.form.controls.key.value).toBe('');
    expect(element.querySelector('input[formControlName="key"]')).toBeNull();
    app.form.controls.connector.setValue('coiote');
    app.closeDialog();
    app.openCreate();
    fixture.detectChanges();
    expect(app.form.controls.connector.value).toBe('');
    expect(element.querySelector('select[formControlName="mode"]')).toBeNull();
  });

  it('rejects invalid PSKs, preserves valid credentials, and clears them on close', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.openCreate();
    app.form.patchValue({
      connector: 'coiote',
      endpoint: ' new-device ',
      identity: ' identity ',
      key: '0x1234',
    });
    await app.createDevice();
    expect(api.create).not.toHaveBeenCalled();
    app.form.controls.key.setValue('AABB');
    await app.createDevice();
    expect(api.create).toHaveBeenCalledWith({
      endpoint_name: 'new-device',
      security_mode: 'psk',
      psk_identity: ' identity ',
      psk_key: 'AABB',
    });
    expect(app.form.controls.key.value).toBe('');
    expect(app.form.controls.identity.value).toBe('');
    expect(app.devices()).toHaveLength(2);
  });

  it('omits credentials in no-security mode', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.openCreate();
    app.form.patchValue({
      connector: 'coiote',
      endpoint: 'new-device',
      mode: 'nosec',
      identity: 'unused',
      key: 'unused',
    });
    await app.createDevice();
    expect(api.create).toHaveBeenCalledWith({
      endpoint_name: 'new-device',
      security_mode: 'nosec',
    });
  });

  it('blocks repeat creation after uncertain or locally-saved upstream failure', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.openCreate();
    app.form.patchValue({
      connector: 'coiote',
      endpoint: 'new-device',
      identity: 'identity',
      key: 'AABB',
    });
    api.create.mockRejectedValue(
      new HttpErrorResponse({
        status: 502,
        error: { detail: 'Saved locally, but Coiote request failed' },
      }),
    );
    await app.createDevice();
    expect(app.createBlocked()).toBe(true);
    expect(app.modalError()).toContain('Saved locally');
    expect(app.form.controls.key.value).toBe('');
    await app.createDevice();
    expect(api.create).toHaveBeenCalledTimes(1);
  });

  it('renames only the friendly name and updates the table after success', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.openRename(device);
    app.renameForm.controls.name.setValue('  Updated  ');
    await app.renameDevice();
    expect(api.rename).toHaveBeenCalledWith('lab-device', 'Updated');
    expect(app.devices()[0].endpoint_name).toBe('lab-device');
    expect(app.devices()[0].friendly_name).toBe('Updated');
  });

  it('requires exact delete confirmation and retains the row when deletion fails', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    app.openDelete(device);
    app.deleteConfirm.setValue('wrong');
    await app.deleteDevice();
    expect(api.delete).not.toHaveBeenCalled();
    app.deleteConfirm.setValue(device.endpoint_name);
    api.delete.mockRejectedValue(
      new HttpErrorResponse({ status: 502, error: { detail: 'Coiote unavailable' } }),
    );
    await app.deleteDevice();
    expect(app.devices()).toHaveLength(1);
    expect(app.modalError()).toBe('Coiote unavailable');
    api.delete.mockResolvedValue(undefined);
    await app.deleteDevice();
    expect(app.devices()).toEqual([]);
  });

  it('submits the actual delete form without navigating the page', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    app.openDelete(device);
    app.deleteConfirm.setValue(device.endpoint_name);
    fixture.detectChanges();
    const form = element.querySelector('dialog form')!;
    const event = new Event('submit', { bubbles: true, cancelable: true });
    form.dispatchEvent(event);
    await fixture.whenStable();
    expect(event.defaultPrevented).toBe(true);
    expect(api.delete).toHaveBeenCalledWith('lab-device');
    expect(app.devices()).toEqual([]);
  });

  it('navigates to a dedicated device URL instead of opening the old drawer', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    const navigation = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    await app.openDetails(device);
    expect(navigation).toHaveBeenCalledWith(['/devices', 'lab-device']);
    expect(api.detail).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('dialog').open).toBe(false);
  });

  it('switches between the routed page and inventory and refreshes inventory on return', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    await app.openDetails(device);
    await fixture.whenStable();
    fixture.detectChanges();
    expect(app.devicePage()).toBe(true);
    expect(element.querySelector('app-device-page')).not.toBeNull();
    expect(element.querySelector('.inventory-toolbar')).toBeNull();
    expect(api.detail).toHaveBeenCalledWith('lab-device');
    expect(api.list).toHaveBeenCalledTimes(1);
    expect(element.querySelector('#overview')).not.toBeNull();
    expect(element.querySelector('#settings')).toBeNull();
    expect(api.serverSettings).not.toHaveBeenCalled();
    expect(api.readLifetime).not.toHaveBeenCalled();
    const settingsLink = element.querySelector<HTMLAnchorElement>(
      '.section-nav a[href$="?view=settings"]',
    )!;
    settingsLink.click();
    await fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/devices/lab-device?view=settings');
    expect(settingsLink.getAttribute('aria-current')).toBe('page');
    expect(element.querySelector('#overview')).toBeNull();
    expect(element.querySelector('#settings #lwm2m-server-heading')).not.toBeNull();
    expect(api.serverSettings).toHaveBeenCalledExactlyOnceWith('lab-device');
    expect(api.readLifetime).toHaveBeenCalledExactlyOnceWith('lab-device');
    expect(api.detail).toHaveBeenCalledTimes(1);
    element.querySelector<HTMLButtonElement>('[data-setting-row="1"] .setting-edit')!.click();
    await fixture.whenStable();
    const lifetime = element.querySelector<HTMLInputElement>('#lifetime-seconds')!;
    lifetime.value = '120';
    lifetime.dispatchEvent(new Event('input', { bubbles: true }));
    const overviewLink = element.querySelector<HTMLAnchorElement>('.section-nav a')!;
    overviewLink.click();
    await fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/devices/lab-device');
    expect(overviewLink.getAttribute('aria-current')).toBe('page');
    expect(element.querySelector('#overview')).not.toBeNull();
    expect(element.querySelector('#settings')).toBeNull();
    settingsLink.click();
    await fixture.whenStable();
    expect(element.querySelector<HTMLInputElement>('#lifetime-seconds')?.value).toBe('120');
    expect(api.serverSettings).toHaveBeenCalledTimes(1);
    expect(api.readLifetime).toHaveBeenCalledTimes(2);
    app.navigate('devices');
    await fixture.whenStable();
    fixture.detectChanges();
    expect(app.devicePage()).toBe(false);
    expect(element.querySelector('app-device-page')).toBeNull();
    expect(element.querySelector('.inventory-toolbar')).not.toBeNull();
    expect(api.list).toHaveBeenCalledTimes(2);
  });

  it('loads inventory automatically without exposing a reload button', async () => {
    const { fixture, api, element } = setup();
    await fixture.whenStable();
    fixture.detectChanges();
    expect(element.querySelectorAll('.heading-actions button')).toHaveLength(1);
    expect(element.querySelector('.inventory-toolbar button')?.textContent).toContain('Sync devices');
    expect(api.list).toHaveBeenCalledTimes(1);
  });

  it('syncs once, keeps the list visible while waiting, and reloads with summary counts', async () => {
    const { fixture, app, api, element } = setup();
    await fixture.whenStable();
    let finish!: (value: { synced: string[]; updated: string[]; missing_from_coiote: string[] }) => void;
    api.sync.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const button = element.querySelector<HTMLButtonElement>('.inventory-toolbar button')!;
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(button.disabled).toBe(false);
    });
    button.click();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(button.disabled).toBe(true);
    });
    expect(button.textContent).toContain('Syncing');
    expect(element.querySelector('tbody')?.textContent).toContain('lab-device');
    await app.syncDevices();
    expect(api.sync).toHaveBeenCalledTimes(1);
    expect(api.list).toHaveBeenCalledTimes(1);
    api.list.mockResolvedValueOnce([{ ...device, endpoint_name: 'new-device' }]);
    finish({ synced: ['new-device'], updated: ['lab-device'], missing_from_coiote: ['missing'] });
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(element.querySelector('tbody')?.textContent).toContain('new-device');
      expect(button.disabled).toBe(false);
    });
    expect(api.list).toHaveBeenCalledTimes(2);
    expect(app.notice()).toContain('1 imported, 1 updated, 1 not found');
  });

  it('retains the list on sync failure and distinguishes failure to reload after syncing', async () => {
    const { fixture, app, api } = setup();
    await fixture.whenStable();
    api.sync.mockRejectedValueOnce(new Error('Connector unavailable'));
    await app.syncDevices();
    expect(app.devices()).toEqual([device]);
    expect(api.list).toHaveBeenCalledTimes(1);
    expect(app.notice()).toContain('Sync failed');
    expect(app.noticeError()).toBe(true);
    expect(app.syncing()).toBe(false);

    api.list.mockRejectedValueOnce(new Error('List unavailable'));
    await app.syncDevices();
    expect(app.devices()).toEqual([device]);
    expect(app.notice()).toContain('Sync completed, but the list could not be reloaded');
    expect(app.noticeError()).toBe(true);
    expect(app.syncing()).toBe(false);
  });
});
