import { clonePersonalization } from '../../apps/framework/frontend/src/core/personalization';
import { describe, expect, it, vi } from 'vitest';
import { BROWSER_UNAVAILABLE, createHostAdapter, DEFAULT_SETTINGS, type InvokeBridge, type Settings, type UIStyle } from '../../apps/framework/frontend/src/core/host';

describe('host adapter boundary', () => {
  it('never simulates desktop or worker success in browser previews', async () => {
    const host = createHostAdapter();
    expect(host.available).toBe(false);
    await expect(host.getInfo()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.showCompanion()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.hideCompanion()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.startCompanionDrag()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.getCompanionState()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.companionReady('example-model')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.getSettings()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.saveSettings({ schemaVersion: 1, theme: 'dark', uiStyle: 'simple', closeToTray: true, personalization: clonePersonalization() })).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.callWorker('runtime.ping')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.stopWorker()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.frontendReady()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.listPackages()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.importPackage()).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.openPackage('test-lab')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.selectPackage('test-lab', '1.0.0', 'digest')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.setPackageEnabled('test-lab', true)).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.callPackage('token', 'test.echo')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.reportPackageReady('token', 'test-lab', '1.0.0', 'digest')).rejects.toThrow(BROWSER_UNAVAILABLE);
    await expect(host.reportPackageLoadFailed('token', 'UI failed')).rejects.toThrow(BROWSER_UNAVAILABLE);
  });
  it('passes structured worker messages through the desktop bridge', async () => {
    const bridge = vi.fn().mockResolvedValue({ message: 'pong' }); const host = createHostAdapter(bridge as InvokeBridge);
    expect(host.available).toBe(true);
    await expect(host.callWorker('runtime.ping')).resolves.toEqual({ message: 'pong' });
    expect(bridge).toHaveBeenCalledWith('worker_call', { method: 'runtime.ping', params: {} });
  });
  it('preserves failures from the desktop host', async () => {
    const host = createHostAdapter(vi.fn().mockRejectedValue('worker unavailable') as InvokeBridge);
    await expect(host.callWorker('runtime.ping')).rejects.toBe('worker unavailable');
  });
  it('keeps the default layout independent of the system color theme', () => {
    expect(DEFAULT_SETTINGS).toEqual({ schemaVersion: 1, theme: 'system', uiStyle: 'simple', closeToTray: true, personalization: clonePersonalization() });
  });
  it.each<UIStyle>(['simple', 'codex', 'cursor', 'web', 'anime', 'glass'])('passes the %s style and color theme unchanged through settings IPC', async (uiStyle) => {
    const settings: Settings = { schemaVersion: 1, theme: 'ocean', uiStyle, closeToTray: false, personalization: clonePersonalization() };
    const bridge = vi.fn().mockResolvedValue(settings);
    const host = createHostAdapter(bridge as InvokeBridge);
    await expect(host.getSettings()).resolves.toBe(settings);
    expect(bridge).toHaveBeenNthCalledWith(1, 'settings_get', undefined);
    await expect(host.saveSettings(settings)).resolves.toBe(settings);
    expect(bridge).toHaveBeenNthCalledWith(2, 'settings_save', { settings });
  });
  it('reports pre-ready failure for the bound token without substituting a close operation', async () => {
    const catalog = { hostApi: 1, modules: [], imports: [] };
    const bridge = vi.fn().mockResolvedValue(catalog);
    const host = createHostAdapter(bridge as InvokeBridge);
    await expect(host.reportPackageLoadFailed('failed-instance-token', 'UI_VERSION_MISMATCH')).resolves.toBe(catalog);
    expect(bridge).toHaveBeenCalledExactlyOnceWith('modules_load_failed', { token: 'failed-instance-token', reason: 'UI_VERSION_MISMATCH' });
  });
});
