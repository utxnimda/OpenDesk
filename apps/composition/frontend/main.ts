import { createApp } from 'vue';
import App from '../../framework/frontend/src/App.vue';
import DesktopCompanionApp from '../../framework/frontend/src/companions/DesktopCompanionApp.vue';
import DesktopCompanionMenuApp from '../../framework/frontend/src/companions/DesktopCompanionMenuApp.vue';
import { companions } from './companions';
import { installContextMenuPolicy } from '../../framework/frontend/src/core/context-menu';
import '../../framework/frontend/src/styles.css';
import '../../framework/frontend/src/companions/companion.css';

const releaseContextMenuPolicy = installContextMenuPolicy(document);
if (import.meta.hot) import.meta.hot.dispose(releaseContextMenuPolicy);

const surface = new URLSearchParams(window.location.search).get('surface');
if (surface === 'companion-menu') {
  document.documentElement.dataset.surface = 'companion-menu';
  createApp(DesktopCompanionMenuApp, { catalog: companions }).mount('#app');
} else if (surface === 'companion') {
  document.documentElement.dataset.surface = 'companion';
  createApp(DesktopCompanionApp, { catalog: companions }).mount('#app');
} else {
  createApp(App, { companions }).mount('#app');
}
