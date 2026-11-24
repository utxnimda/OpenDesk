import { createApp } from 'vue';
import { createModuleBridge } from '@opendesk/module-sdk';
import App from './App.vue';
import './style.css';

const bridge = createModuleBridge();
const app = createApp(App, { bridge });
app.mount('#app');

window.addEventListener(
  'pagehide',
  () => {
    bridge.dispose();
  },
  { once: true },
);
