import { createApp } from 'vue';
import App from '../../framework/frontend/src/App.vue';
import DesktopCompanionApp from '../../framework/frontend/src/companions/DesktopCompanionApp.vue';
import { companions } from './companions';
import '../../framework/frontend/src/styles.css';
import '../../framework/frontend/src/companions/companion.css';

if (new URLSearchParams(window.location.search).get('surface') === 'companion') {
  document.documentElement.dataset.surface = 'companion';
  createApp(DesktopCompanionApp, { catalog: companions }).mount('#app');
} else {
  createApp(App, { companions }).mount('#app');
}
