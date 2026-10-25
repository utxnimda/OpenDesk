import { createApp } from 'vue';
import { createModuleBridge, errorMessage } from '../../../framework/module-sdk/src/index';
import TowerAssistantPage from './TowerAssistantPage.vue';
import StatisticsOverlay from './StatisticsOverlay.vue';
import '../../../framework/frontend/src/styles.css';
import '../../../framework/module-sdk/src/surface.css';

const bridge = createModuleBridge();
bridge.ready.then(() => {
  const surface = bridge.host.surface?.context;
  if (surface) {
    if (surface.view !== 'statistics' || typeof surface.data.gameDir !== 'string' || !surface.data.gameDir.trim()) throw new Error('无法打开此独立视图：游戏目录或视图类型无效。');
    document.documentElement.dataset.surface = 'statistics-overlay';
    createApp(StatisticsOverlay, { host: bridge.host, gameDir: surface.data.gameDir,
      initialKind: surface.data.kind === 'work' ? 'work' : 'damage',
      initialNumberMode: surface.data.numberMode === 'raw' ? 'raw' : 'compact',
      initialRankingFilter: surface.data.rankingFilter }).mount('#app');
    return;
  }
  createApp(TowerAssistantPage, { services: { host: bridge.host } }).mount('#app');
}).catch((error) => {
  const target = document.getElementById('app');
  if (target) { target.className = 'alert alert-error'; target.textContent = errorMessage(error); }
});
