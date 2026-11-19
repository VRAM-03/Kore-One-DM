import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, timeout } from 'rxjs';

export interface Device {
  id: number;
  endpoint_name: string;
  friendly_name: string | null;
  security_mode: string | null;
  coiote_device_id: string | null;
  onboarding_status: string | null;
  connection_status: string | null;
  refreshed_at: string | null;
  first_registered_at: string | null;
  last_registered_at: string | null;
  last_seen_at: string | null;
  created_at: string | null;
}

export interface DeviceDetails {
  local: Device;
  coiote_snapshot: {
    fetched_at: string;
    device_id: string | null;
    endpoint_name: string | null;
    friendly_name: string | null;
    connector_type: string | null;
    domain: string | null;
    security_mode: string | null;
  } | null;
}

export interface DeviceLastSeen {
  last_seen_at: string | null;
}

export interface DeviceSyncResult {
  synced: string[];
  updated: string[];
  missing_from_coiote: string[];
}

export interface CreateDevice {
  endpoint_name: string;
  friendly_name?: string;
  security_mode: 'psk' | 'nosec';
  psk_identity?: string;
  psk_key?: string;
}

export interface RegistrationLifetimeResult {
  task_id: string;
  device_id: string;
  resource: string;
  operation: 'read' | 'write';
  state: 'succeeded' | 'failed' | 'verification_failed';
  requested_lifetime: number | null;
  lifetime: number | null;
  matches_requested: boolean | null;
  observed_at: string | null;
}

export type ServerSettingValue = boolean | number | string;

export interface ServerSetting {
  resource_id: number;
  label: string;
  data_type: 'boolean' | 'integer' | 'string';
  unit: string | null;
  minimum: number | null;
  maximum: number | null;
  warning: string | null;
  resource: string | null;
  readable: boolean;
  writable: boolean;
  value: ServerSettingValue | null;
  observed_at: string | null;
}

export interface DeviceObservation {
  name: string;
  value: string;
  updateTime: string;
}

export interface DeviceSetting {
  resource_id: number;
  label: string;
  readable: boolean;
  executable: boolean;
  observations: DeviceObservation[];
}

export interface ServerSettingOperation {
  id: string;
  resource_id: number;
  resource: string;
  kind: 'read' | 'write' | 'reboot';
  state: 'pending' | 'submission_unknown' | 'succeeded' | 'failed' | 'verification_failed';
  requested_value: ServerSettingValue | null;
  value: ServerSettingValue | null;
  observed_at: string | null;
  error: string | null;
  observations?: DeviceObservation[];
}

export interface ServerSettingsResponse {
  settings: ServerSetting[];
  unresolved_operation: ServerSettingOperation | null;
  completed_operation?: ServerSettingOperation | null;
}

@Injectable({ providedIn: 'root' })
export class DeviceApi {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  list(): Promise<Device[]> {
    return firstValueFrom(this.http.get<Device[]>(`${this.base}/devices`));
  }

  sync(): Promise<DeviceSyncResult> {
    return firstValueFrom(this.http.post<DeviceSyncResult>(`${this.base}/sync-devices`, null));
  }

  detail(endpoint: string): Promise<DeviceDetails> {
    return firstValueFrom(this.http.get<DeviceDetails>(this.deviceUrl(endpoint)));
  }

  lastSeen(endpoint: string): Promise<DeviceLastSeen> {
    return firstValueFrom(
      this.http.get<DeviceLastSeen>(`${this.deviceUrl(endpoint)}/last-seen`).pipe(timeout(20000)),
    );
  }

  create(body: CreateDevice): Promise<Device> {
    return firstValueFrom(this.http.post<Device>(`${this.base}/devices`, body));
  }

  rename(endpoint: string, friendlyName: string): Promise<Device> {
    return firstValueFrom(
      this.http.patch<Device>(this.deviceUrl(endpoint), {
        friendly_name: friendlyName,
      }),
    );
  }

  delete(endpoint: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(this.deviceUrl(endpoint)));
  }

  readLifetime(endpoint: string): Promise<RegistrationLifetimeResult> {
    return firstValueFrom(
      this.http
        .post<RegistrationLifetimeResult>(`${this.deviceUrl(endpoint)}/lifetime/read`, null)
        .pipe(timeout(35000)),
    );
  }

  writeLifetime(endpoint: string, lifetime: number): Promise<RegistrationLifetimeResult> {
    return firstValueFrom(
      this.http
        .patch<RegistrationLifetimeResult>(`${this.deviceUrl(endpoint)}/lifetime`, { lifetime })
        .pipe(timeout(35000)),
    );
  }

  serverSettings(endpoint: string): Promise<ServerSettingsResponse> {
    return firstValueFrom(
      this.http.get<ServerSettingsResponse>(`${this.deviceUrl(endpoint)}/server-settings`),
    );
  }

  deviceSettings(endpoint: string): Promise<DeviceSetting[]> {
    return firstValueFrom(
      this.http.get<DeviceSetting[]>(`${this.deviceUrl(endpoint)}/device-settings`),
    );
  }

  readDeviceSetting(endpoint: string, id: number): Promise<ServerSettingOperation> {
    return firstValueFrom(
      this.http.post<ServerSettingOperation>(
        `${this.deviceUrl(endpoint)}/device-settings/${id}/read`,
        null,
      ),
    );
  }

  rebootDevice(endpoint: string): Promise<ServerSettingOperation> {
    return firstValueFrom(
      this.http.post<ServerSettingOperation>(`${this.deviceUrl(endpoint)}/device-settings/reboot`, {
        confirm: true,
      }),
    );
  }

  readServerSetting(endpoint: string, resourceId: number): Promise<ServerSettingOperation> {
    return firstValueFrom(
      this.http.post<ServerSettingOperation>(
        `${this.deviceUrl(endpoint)}/server-settings/${resourceId}/read`,
        null,
      ),
    );
  }

  writeServerSetting(
    endpoint: string,
    resourceId: number,
    value: ServerSettingValue,
  ): Promise<ServerSettingOperation> {
    return firstValueFrom(
      this.http.patch<ServerSettingOperation>(
        `${this.deviceUrl(endpoint)}/server-settings/${resourceId}`,
        { value },
      ),
    );
  }

  serverSettingOperation(endpoint: string, id: string): Promise<ServerSettingOperation> {
    return firstValueFrom(
      this.http.get<ServerSettingOperation>(
        `${this.deviceUrl(endpoint)}/server-settings/operations/${encodeURIComponent(id)}`,
      ),
    );
  }

  saveCoioteConfiguration(username: string, password: string): Promise<void> {
    return firstValueFrom(
      this.http
        .put<void>(`${this.base}/connectors/coiote/configuration`, { username, password })
        .pipe(timeout(15000)),
    );
  }

  validateCoioteCredentials(username: string, password: string): Promise<{ valid: boolean }> {
    return firstValueFrom(
      this.http
        .post<{ valid: boolean }>(`${this.base}/connectors/coiote/validate`, { username, password })
        .pipe(timeout(15000)),
    );
  }

  health(): Promise<{ status: string }> {
    return firstValueFrom(
      this.http.get<{ status: string }>(`${this.base}/health`).pipe(timeout(10000)),
    );
  }

  private deviceUrl(endpoint: string): string {
    return `${this.base}/devices/${encodeURIComponent(endpoint)}`;
  }
}

export function isRegistrationLifetimeResult(value: unknown): value is RegistrationLifetimeResult {
  if (typeof value !== 'object' || value === null) return false;
  const result = value as Record<string, unknown>;
  const isLifetime = (candidate: unknown): boolean =>
    candidate === null ||
    (typeof candidate === 'number' &&
      Number.isSafeInteger(candidate) &&
      candidate >= 1 &&
      candidate <= 4294967295);
  return (
    typeof result['task_id'] === 'string' &&
    typeof result['device_id'] === 'string' &&
    typeof result['resource'] === 'string' &&
    (result['operation'] === 'read' || result['operation'] === 'write') &&
    (result['state'] === 'succeeded' ||
      result['state'] === 'failed' ||
      result['state'] === 'verification_failed') &&
    isLifetime(result['requested_lifetime']) &&
    isLifetime(result['lifetime']) &&
    (result['matches_requested'] === null || typeof result['matches_requested'] === 'boolean') &&
    (result['observed_at'] === null || typeof result['observed_at'] === 'string')
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 0) {
      return 'Cannot reach the backend. Check the connection. A submitted change may have reached the server; verify it before retrying.';
    }
    const body: unknown = error.error;
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const detail = body.detail;
      if (typeof detail === 'string') return detail;
      if (
        typeof detail === 'object' &&
        detail !== null &&
        'message' in detail &&
        typeof detail.message === 'string'
      )
        return detail.message;
      if (Array.isArray(detail)) {
        const messages = detail.flatMap((item: unknown) =>
          typeof item === 'object' && item !== null && 'msg' in item && typeof item.msg === 'string'
            ? [item.msg]
            : [],
        );
        if (messages.length) return messages.join(' · ');
      }
    }
    return `Backend request failed (HTTP ${error.status}). No success has been confirmed.`;
  }
  return 'The request could not be completed. Verify the backend connection before retrying.';
}

export function submissionOperationId(error: unknown): string | null {
  if (!(error instanceof HttpErrorResponse)) return null;
  const body: unknown = error.error;
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null;
  const detail = body.detail;
  return typeof detail === 'object' &&
    detail !== null &&
    'operation_id' in detail &&
    typeof detail.operation_id === 'string' &&
    detail.operation_id.length > 0
    ? detail.operation_id
    : null;
}
