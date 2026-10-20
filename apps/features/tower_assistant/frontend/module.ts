import type { ModuleDefinition } from '../../../framework/frontend/src/core/modules';

export default {
  id: 'tower-assistant',
  label: '塔防山贼助手',
  description: '读取存档，分析灵物与渡劫布局，生成迁移对照图。',
  order: 100,
  load: () => import('./TowerAssistantPage.vue'),
} satisfies ModuleDefinition;
