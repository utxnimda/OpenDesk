import type { ModuleDefinition } from '../../../framework/frontend/src/core/modules';

export default {
  id: 'tower-assistant',
  label: '塔防山贼助手',
  description: '查看灵物与渡劫布局，统计人物工作输出和逐次伤害，展开伤害公式。',
  order: 100,
  load: () => import('./TowerAssistantPage.vue'),
} satisfies ModuleDefinition;
