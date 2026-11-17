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

export interface CreateDevice {
  endpoint_name: string;
  friendly_name?: string;
  security_mode: 'psk' | 'nosec';
  psk_identity?: string;
  psk_key?: string;
}

export type ResourceKey =
  | 'Device.0.Manufacturer'
  | 'Device.0.Model Number'
  | 'Device.0.Firmware Version'
  | 'Device.0.Timezone';

export interface DeviceResource {
  key: string;
  resource_id: number;
  data_type: string;
  operations: ('Read' | 'Write' | 'Execute')[];
}

export interface DeviceOperation {
  id: string;
  resource_key: ResourceKey;
  kind: 'read' | 'write';
  requested_value: string | null;
  status:
    | 'submitting'
    | 'submission_unknown'
    | 'rejected'
    | 'pending'
    | 'succeeded'
    | 'failed'
    | 'not_found';
  value: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

@Injectable({ providedIn: 'root' })
export class DeviceApi {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  list(): Promise<Device[]> {
    return firstValueFrom(this.http.get<Device[]>(`${this.base}/devices`));
  }

  detail(endpoint: string): Promise<DeviceDetails> {
    return firstValueFrom(this.http.get<DeviceDetails>(this.deviceUrl(endpoint)));
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

  resources(endpoint: string): Promise<{ resources: DeviceResource[] }> {
    return firstValueFrom(
      this.http.get<{ resources: DeviceResource[] }>(`${this.deviceUrl(endpoint)}/resources`),
    );
  }

  read(endpoint: string, resourceKey: ResourceKey): Promise<DeviceOperation> {
    return firstValueFrom(
      this.http.post<DeviceOperation>(`${this.deviceUrl(endpoint)}/reads`, {
        resource_key: resourceKey,
      }),
    );
  }

  writeTimezone(endpoint: string, value: string): Promise<DeviceOperation> {
    return firstValueFrom(
      this.http.post<DeviceOperation>(`${this.deviceUrl(endpoint)}/writes`, {
        resource_key: 'Device.0.Timezone',
        value,
      }),
    );
  }

  operation(endpoint: string, id: string): Promise<DeviceOperation> {
    return firstValueFrom(
      this.http.get<DeviceOperation>(
        `${this.deviceUrl(endpoint)}/operations/${encodeURIComponent(id)}`,
      ),
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
