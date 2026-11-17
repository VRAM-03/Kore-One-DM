import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withInMemoryScrolling } from '@angular/router';
import { provideKeycloak } from 'keycloak-angular';
import Keycloak from 'keycloak-js';
import { DevicePage } from './device-page';

export async function initializeKeycloak(): Promise<void> {
  const authenticated = await inject(Keycloak).init({
    onLoad: 'login-required',
    pkceMethod: 'S256',
  });
  if (!authenticated) {
    throw new Error('Keycloak authentication is required to start the application.');
  }
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideKeycloak({
      config: {
        url: 'https://sso-dev.korewireless.com',
        realm: 'KORE-CIDP',
        clientId: 'chts-diagnostic-tool-dev',
      },
    }),
    provideAppInitializer(initializeKeycloak),
    provideHttpClient(),
    provideRouter(
      [
        { path: 'devices/:endpoint', component: DevicePage },
        { path: '', children: [] },
        { path: '**', redirectTo: '' },
      ],
      withInMemoryScrolling({ anchorScrolling: 'enabled', scrollPositionRestoration: 'enabled' }),
    ),
  ],
};
