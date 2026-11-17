import { TestBed } from '@angular/core/testing';
import Keycloak from 'keycloak-js';
import { describe, expect, it, vi } from 'vitest';
import { initializeKeycloak } from './app.config';

describe('Keycloak initialization', () => {
  it('requires login and waits for authentication before starting', async () => {
    const init = vi.fn().mockResolvedValue(true);
    TestBed.configureTestingModule({ providers: [{ provide: Keycloak, useValue: { init } }] });

    await expect(TestBed.runInInjectionContext(initializeKeycloak)).resolves.toBeUndefined();
    expect(init).toHaveBeenCalledWith({ onLoad: 'login-required', pkceMethod: 'S256' });
  });

  it('prevents startup when authentication fails or initialization errors', async () => {
    const init = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockRejectedValueOnce(new Error('SSO unavailable'));
    TestBed.configureTestingModule({ providers: [{ provide: Keycloak, useValue: { init } }] });

    await expect(TestBed.runInInjectionContext(initializeKeycloak)).rejects.toThrow(
      'Keycloak authentication is required',
    );
    await expect(TestBed.runInInjectionContext(initializeKeycloak)).rejects.toThrow(
      'SSO unavailable',
    );
  });
});
