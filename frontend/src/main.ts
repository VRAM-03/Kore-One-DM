import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

bootstrapApplication(App, appConfig).catch((err) => {
  console.error('Application startup failed:', err);
  const root = document.querySelector('app-root');
  if (root) {
    root.textContent = 'Unable to start the application. Check the browser console and try again.';
  }
});
