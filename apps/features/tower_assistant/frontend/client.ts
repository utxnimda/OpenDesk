import { BROWSER_UNAVAILABLE, type LimitedHost } from '../../../framework/module-sdk/src/index';
import type { Analysis, Inspection, VisualCatalog } from './types';

/** The worker owns the read-only snapshot and plan. These values are display caches only. */
export function createTowerClient(host: LimitedHost) {
  async function call<T>(method: string, params: Record<string, unknown>): Promise<T> {
    if (!host.available) throw new Error(BROWSER_UNAVAILABLE);
    return host.callWorker<T>(method, params);
  }
  function directory(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) throw new Error('请填写游戏安装目录。');
    return trimmed;
  }
  function versioned<T extends { schemaVersion: number }>(response: T): T {
    if (!response || response.schemaVersion !== 1) throw new Error('游戏工具返回了不支持的数据版本，请更新应用后重试。');
    return response;
  }
  return {
    discover: () => call<{ gameDirs: string[] }>('tower.discover', {}),
    visuals: async (gameDir: string, iconKeys: string[]): Promise<VisualCatalog> => {
      const path = directory(gameDir);
      if (!host.available) throw new Error(BROWSER_UNAVAILABLE);
      const keys = [...new Set(iconKeys)];
      if (keys.length > 1024) throw new Error('所需图标过多，请缩小读取范围后重试。');
      const result: VisualCatalog = { schemaVersion: 1, sheets: {}, sprites: {}, warnings: [] };
      for (let offset = 0; offset < keys.length; offset += 32) {
        const batch = versioned(await call<VisualCatalog>('tower.visuals', { gameDir: path, iconKeys: keys.slice(offset, offset + 32) }));
        Object.assign(result.sheets, batch.sheets);
        Object.assign(result.sprites, batch.sprites);
        result.warnings!.push(...(batch.warnings ?? []));
      }
      result.warnings = [...new Set(result.warnings)];
      return result;
    },
    inspect: async (gameDir: string, saveId?: string) => versioned(await call<Inspection>('tower.inspect', {
      gameDir: directory(gameDir), ...(saveId ? { saveId } : {}),
    })),
    analyze: async (gameDir: string, saveId?: string, platformId?: string) => versioned(await call<Analysis>('tower.analyze', {
      gameDir: directory(gameDir),
      ...(saveId ? { saveId } : {}),
      ...(platformId ? { platformId } : {}),
    })),
  };
}
