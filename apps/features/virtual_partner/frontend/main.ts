import { createApp } from 'vue';
import { createModuleBridge, errorMessage } from '../../../framework/module-sdk/src/index';
import '../../../framework/frontend/src/styles.css';
import '../../../framework/module-sdk/src/surface.css';
import './style.css';
import PartnerPage from './PartnerPage.vue';

const bridge = createModuleBridge({petPortraits: true});
bridge.ready.then(() => createApp(PartnerPage, { host: bridge.host }).mount('#app')).catch((error) => {
  const outlet = document.getElementById('app');
  if (outlet) { outlet.setAttribute('role', 'alert'); outlet.textContent = errorMessage(error); }
});
