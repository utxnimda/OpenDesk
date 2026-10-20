import type { ModuleDefinition } from '../../../../framework/frontend/src/core/modules';

export default {
  id: 'dev-example',
  label: '开发示例',
  description: '验证独立模块与宿主契约，仅在开发环境显式启用。',
  order: 1000,
  developmentOnly: true,
  load: () => import('./ExamplePage.vue'),
} satisfies ModuleDefinition;
