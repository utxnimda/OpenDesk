import { createApp } from 'vue';
import { createModuleBridge, errorMessage } from '../../../framework/module-sdk/src/index';
import TestLabPage from './TestLabPage.vue';
import '../../../framework/frontend/src/styles.css';
import '../../../framework/module-sdk/src/surface.css';

const bridge = createModuleBridge();
bridge.ready.then((identity) => {
  createApp(TestLabPage, { host: bridge.host, identity }).mount('#app');
}).catch((error) => {
  const target = document.getElementById('app');
  if (target) { target.className = 'alert alert-error'; target.textContent = errorMessage(error); }
});
