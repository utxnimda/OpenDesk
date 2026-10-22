import { BRIDGE_PROTOCOL, errorMessage, getBaseTheme, isRecord, matchesSource, resolveMessageSurfaceEffects } from '../../../module-sdk/src/index';
import type { Appearance, SurfaceEffects, UIStyle } from '../../../module-sdk/src/index';

/** Protocol 1 keeps its base theme valid for already installed module SDKs. */
export function packageAppearance(appearance: Appearance, uiStyle: UIStyle = 'simple', surfaceEffects?: SurfaceEffects): { theme: 'light' | 'dark'; appearance: Appearance; uiStyle: UIStyle; surfaceEffects: SurfaceEffects } {
  return { theme: getBaseTheme(appearance), appearance, uiStyle, surfaceEffects: resolveMessageSurfaceEffects(surfaceEffects) };
}

export function isModuleHello(event: { origin: string; source: unknown; data: unknown }, expectedSource: unknown, expectedOrigin: string, generation: string): boolean {
  return matchesSource(event, expectedSource, expectedOrigin) && isRecord(event.data)
    && event.data.type === 'nimdadesk:hello' && event.data.protocol === BRIDGE_PROTOCOL && event.data.generation === generation;
}

/** A broker is bound to a host-owned token by its caller, never by module input. */
export function createPackageBroker(generation: string, call: (method: string, params: Record<string, unknown>) => Promise<unknown>, onReady: (identity: Record<string, unknown>) => void | Promise<void>) {
  let active = true;
  let ready = false;
  let preparing = false;
  const pending = new Set<string>();
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
      if (data.type !== 'call' || typeof data.id !== 'string' || data.id.length > 200) return;
      const id = data.id;
      if (!ready) { reply({ type: 'result', generation, id, ok: false, error: 'MODULE_NOT_READY: 尚未完成模块身份校验。' }); return; }
      if (pending.has(id)) return;
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
    dispose() { active = false; pending.clear(); },
  };
}
