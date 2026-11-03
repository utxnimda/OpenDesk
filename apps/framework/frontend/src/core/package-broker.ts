import { BRIDGE_PROTOCOL, errorMessage, getBaseTheme, isRecord, matchesSource, resolveMessageSurfaceEffects } from '../../../module-sdk/src/index';
import type { Appearance, SurfaceEffects, UIStyle } from '../../../module-sdk/src/index';
import { parseToolOperation, type ToolOperation } from '../../../module-sdk/src/module-tools';
import { parseSurfaceOperation, type SurfaceOperation } from '../../../module-sdk/src/module-surface';
import { parsePetPortraitOperation, type PetPortraitOperation } from '../../../module-sdk/src/pet-portraits';

/** Protocol 1 keeps its base theme valid for already installed module SDKs. */
export function packageAppearance(appearance: Appearance, uiStyle: UIStyle = 'simple', surfaceEffects?: SurfaceEffects): { theme: 'light' | 'dark'; appearance: Appearance; uiStyle: UIStyle; surfaceEffects: SurfaceEffects } {
  return { theme: getBaseTheme(appearance), appearance, uiStyle, surfaceEffects: resolveMessageSurfaceEffects(surfaceEffects) };
}

export function isModuleHello(event: { origin: string; source: unknown; data: unknown }, expectedSource: unknown, expectedOrigin: string, generation: string): boolean {
  return matchesSource(event, expectedSource, expectedOrigin) && isRecord(event.data)
    && event.data.type === 'nimdadesk:hello' && event.data.protocol === BRIDGE_PROTOCOL && event.data.generation === generation;
}

/** A broker is bound to a host-owned token by its caller, never by module input. */
export function createPackageBroker(generation: string, call: (method: string, params: Record<string, unknown>) => Promise<unknown>, onReady: (identity: Record<string, unknown>) => void | Promise<void>, surface?: (operation: SurfaceOperation) => Promise<unknown>, tools?: (operation: ToolOperation) => Promise<unknown>, portraits?: (operation: PetPortraitOperation, signal: AbortSignal) => Promise<unknown>) {
  let active = true;
  let ready = false;
  let preparing = false;
  const pending = new Set<string>();
  const lifetime = new AbortController();
  let portraitWindow = 0;
  let portraitRequests = 0;
  return {
    async receive(data: unknown, reply: (value: unknown) => void): Promise<void> {
      if (!active || !isRecord(data) || data.generation !== generation) return;
      if (data.type === 'ready') {
        if (ready || preparing) return;
        preparing = true;
        try {
          await onReady(data);
          if (active) { ready = true; reply({ type: 'accepted', generation }); }
        } catch (error) { if (active) reply({ type: 'host-error', generation, error: errorMessage(error) }); }
        finally { preparing = false; }
        return;
      }
      if ((data.type !== 'call' && data.type !== 'surface' && data.type !== 'tools' && data.type !== 'pet-portraits') || typeof data.id !== 'string' || data.id.length > 200) return;
      const id = data.id;
      if (!ready) { reply({ type: 'result', generation, id, ok: false, error: 'MODULE_NOT_READY: 尚未完成模块身份校验。' }); return; }
      if (pending.has(id)) return;
      if (data.type === 'tools' || data.type === 'pet-portraits') {
        try {
          pending.add(id);
          let result: unknown;
          if (data.type === 'tools') {
            if (!tools) throw new Error('当前宿主不支持插件工具。');
            result = await tools(parseToolOperation(data.action, data.params));
          } else {
            if (!portraits) throw new Error('此视图未连接桌宠头像接口。');
            const now = performance.now();
            if (now - portraitWindow >= 1000) { portraitWindow = now; portraitRequests = 0; }
            if (++portraitRequests > 32) throw new Error('读取桌宠头像过于频繁，请稍后重试。');
            result = await portraits(parsePetPortraitOperation(data.action, data.params), lifetime.signal);
          }
          if (active) reply({ type: 'result', generation, id, ok: true, result });
        } catch (error) { if (active) reply({ type: 'result', generation, id, ok: false, error: errorMessage(error) }); }
        finally { pending.delete(id); }
        return;
      }
      if (data.type === 'surface') {
        try {
          if (!surface) throw new Error('当前宿主不支持此独立窗口操作。');
          const operation = parseSurfaceOperation(data.action, data.params);
          pending.add(id);
          const result = await surface(operation);
          if (active) reply({ type: 'result', generation, id, ok: true, result });
        } catch (error) {
          if (active) reply({ type: 'result', generation, id, ok: false, error: errorMessage(error) });
        } finally { pending.delete(id); }
        return;
      }
      if (typeof data.method !== 'string' || !/^[a-z][a-z0-9_.-]{0,127}$/.test(data.method) || !isRecord(data.params)) {
        reply({ type: 'result', generation, id, ok: false, error: 'INVALID_REQUEST: 模块消息格式错误。' }); return;
      }
      try {
        if (new TextEncoder().encode(JSON.stringify(data.params)).byteLength > 65_536) throw new Error('模块请求超过大小限制。');
        pending.add(id);
        const result = await call(data.method, data.params);
        if (active) reply({ type: 'result', generation, id, ok: true, result });
      } catch (error) {
        if (active) reply({ type: 'result', generation, id, ok: false, error: errorMessage(error) });
      } finally { pending.delete(id); }
    },
    dispose() { active = false; lifetime.abort(); pending.clear(); },
  };
}
