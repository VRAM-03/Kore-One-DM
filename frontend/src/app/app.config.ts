import { DOCUMENT } from '@angular/common';
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
import { SIGNED_OUT_PATH } from './auth';
import { DevicePage } from './device-page';

export async function initializeKeycloak(): Promise<void> {
  const signedOut = inject(DOCUMENT).location?.pathname === `/${SIGNED_OUT_PATH}`;
  const authenticated = await inject(Keycloak).init({
    ...(signedOut ? { checkLoginIframe: false } : { onLoad: 'login-required' }),
    pkceMethod: 'S256',
    scope: 'email',
  });
  if (!authenticated && !signedOut) {
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
        clientId: 'nexus-av-connector-dev',
      },
    }),
    provideAppInitializer(initializeKeycloak),
    provideHttpClient(),
    provideRouter(
      [
        { path: 'devices/:endpoint', component: DevicePage },
        { path: SIGNED_OUT_PATH, children: [] },
        { path: '', children: [] },
        { path: '**', redirectTo: '' },
      ],
      withInMemoryScrolling({ anchorScrolling: 'enabled', scrollPositionRestoration: 'enabled' }),
    ),
  ],
};
