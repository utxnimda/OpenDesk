import type { CompanionCatalog } from '../../framework/frontend/src/companions/contracts';
import loop from '../../features/companion/assets/traveler-01/loop.png';
import still from '../../features/companion/assets/traveler-01/still.png';
import amuLoop from '../../features/companion/assets/amu-01/loop.png';
import amuStill from '../../features/companion/assets/amu-01/still.png';

export const companions: CompanionCatalog = Object.freeze({
  defaultId: 'traveler-01',
  models: Object.freeze([
    Object.freeze({
      id: 'traveler-01',
      label: '阿宝',
      description: '由你提供的视频制作：站立—跑步—站立的真实循环动作，可暂停为静态形象。',
      assets: Object.freeze({ idle: loop, still }),
    }),
    Object.freeze({
      id: 'amu-01',
      label: '阿沐',
      description: '由你提供的视频制作：全身骑行的连续短循环，可暂停为静态形象。',
      assets: Object.freeze({ idle: amuLoop, still: amuStill }),
    }),
  ]),
});
