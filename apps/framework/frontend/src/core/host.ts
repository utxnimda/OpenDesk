import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import frameworkDefaults from '../../../../../config/framework/defaults.json';
import type { PackageCatalog, PackageInstance } from './packages';
import type { Personalization } from './personalization';
export type { Personalization, PetSettings } from './personalization';
import type { Appearance, UIStyle } from '../../../module-sdk/src/index';

export type Theme = 'system' | Appearance;
export type { UIStyle } from '../../../module-sdk/src/index';

export interface Settings {
  schemaVersion: 1;
  theme: Theme;
  uiStyle: UIStyle;
  closeToTray: boolean;
  personalization: Personalization;
}

export interface HostInfo {
  appVersion: string;
  platform: string;
  architecture: string;
  dataDir: string;
  workerState: 'stopped' | 'running';
  packaged: boolean;
  moduleSmoke?: boolean;
}

export interface PingResult {
  message: 'pong';
  protocolVersion: 1;
  pid: number;
  pythonVersion: string;
}

export interface HostAdapter {
  readonly available: boolean;
  getInfo(): Promise<HostInfo>;
  getSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<Settings>;
  showCompanion(): Promise<void>;
  hideCompanion(): Promise<void>;
  startCompanionDrag(): Promise<void>;
  getCompanionState(): Promise<{ visible: boolean }>;
  companionReady(modelId: string): Promise<void>;
  subscribeSettingsChanged(callback: () => void): Promise<() => void>;
  subscribeCompanionVisibilityChanged(callback: (visible: boolean) => void): Promise<() => void>;
  callWorker<T>(method: string, params?: Record<string, unknown>): Promise<T>;
  stopWorker(): Promise<void>;
  frontendReady(): Promise<void>;
  listPackages(): Promise<PackageCatalog>;
  importPackage(): Promise<PackageCatalog>;
  selectPackage(id: string, version: string, digest: string): Promise<PackageCatalog>;
  setPackageEnabled(id: string, enabled: boolean): Promise<PackageCatalog>;
  openPackage(id: string): Promise<PackageInstance>;
  callPackage<T>(token: string, method: string, params?: Record<string, unknown>): Promise<T>;
  closePackage(token: string): Promise<void>;
  reportPackageReady(token: string, id: string, version: string, digest: string): Promise<void>;
  reportPackageLoadFailed(token: string, reason: string): Promise<PackageCatalog>;
  subscribePackagesChanged(callback: () => void): Promise<() => void>;
  subscribeSmokeNavigate(callback: (id: string) => void): Promise<() => void>;
}

export type InvokeBridge = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
export type EventBridge = (event: string, callback: (payload: unknown) => void) => Promise<() => void>;

// This is only a form draft until settings_get succeeds. Never persist this fallback.
export const DEFAULT_SETTINGS = Object.freeze(frameworkDefaults as Settings);
export const BROWSER_UNAVAILABLE = '浏览器预览，桌面能力不可用。请通过桌面应用运行此操作。';

/** Only this adapter knows about Tauri. Features consume the framework contract. */
export function createHostAdapter(bridge?: InvokeBridge, events?: EventBridge): HostAdapter {
  async function request<T>(command: string, args?: Record<string, unknown>): Promise<T> {
    if (!bridge) throw new Error(BROWSER_UNAVAILABLE);
    return bridge<T>(command, args);
  }

  return {
    available: Boolean(bridge),
    getInfo: () => request('host_info'),
    getSettings: () => request('settings_get'),
    saveSettings: (settings) => request('settings_save', { settings }),
    showCompanion: () => request('companion_show'),
    hideCompanion: () => request('companion_hide'),
    startCompanionDrag: () => request('companion_start_drag'),
    getCompanionState: () => request('companion_get_state'),
    companionReady: (modelId) => request('companion_ready', { modelId }),
    subscribeSettingsChanged: (callback) => events ? events('settings-changed', callback) : Promise.resolve(() => {}),
    subscribeCompanionVisibilityChanged: (callback) => events ? events('companion-visibility-changed', (value) => { if (typeof value === 'boolean') callback(value); }) : Promise.resolve(() => {}),
    callWorker: (method, params = {}) => request('worker_call', { method, params }),
    stopWorker: () => request('worker_stop'),
    frontendReady: () => request('frontend_ready'),
    listPackages: () => request('modules_list'),
    importPackage: () => request('modules_import'),
    selectPackage: (id, version, digest) => request('modules_select', { id, version, digest }),
    setPackageEnabled: (id, enabled) => request('modules_enable', { id, enabled }),
    openPackage: (id) => request('modules_open', { id }),
    callPackage: (token, method, params = {}) => request('modules_call', { token, method, params }),
    closePackage: (token) => request('modules_close', { token }),
    reportPackageReady: (token, id, version, digest) => request('modules_ui_ready', { token, id, version, digest }),
    reportPackageLoadFailed: (token, reason) => request('modules_load_failed', { token, reason }),
    subscribePackagesChanged: (callback) => events ? events('modules-changed', callback) : Promise.resolve(() => {}),
    subscribeSmokeNavigate: (callback) => events ? events('modules-smoke-navigate', (payload) => {
      const id = typeof payload === 'string' ? payload : typeof payload === 'object' && payload !== null && 'id' in payload ? payload.id : null;
      if (typeof id === 'string') callback(id);
    }) : Promise.resolve(() => {}),
  };
}

const desktop = typeof window !== 'undefined' && isTauri();
export const host = createHostAdapter(desktop ? invoke : undefined, desktop ? (event, callback) => listen(event, (message) => callback(message.payload)) : undefined);

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === 'string' ? error : JSON.stringify(error) || '未知错误';
}
