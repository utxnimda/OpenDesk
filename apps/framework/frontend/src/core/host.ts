import type { HostTool, HostToolIdentity } from '../../../module-sdk/src/module-tools';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import frameworkDefaults from '../../../../../config/framework/defaults.json';
import type { PackageCatalog, PackageInstance } from './packages';
import type { Personalization, PetPreferenceChange } from './personalization';
export type { Personalization, PetSettings } from './personalization';
import type { Appearance, UIStyle } from '../../../module-sdk/src/index';
import type { ModuleSurfaceOptions } from '../../../module-sdk/src/module-surface';

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

export interface CompanionMenuContext { generation: number; modelId: string; canAnimate: boolean }
export interface PackageSurfaceContext { generation: string; instance: PackageInstance; view: string; context: Record<string, unknown>; alwaysOnTop: boolean }

export interface HostAdapter {
  readonly available: boolean;
  listPackageTools?(token: string): Promise<HostTool[]>;
  grantPackageTools?(token: string, tools: HostToolIdentity[]): Promise<void>;
  callPackageTool?(token: string, tool: HostToolIdentity, args: Record<string, unknown>): Promise<unknown>;
  getInfo(): Promise<HostInfo>;
  getSettings(): Promise<Settings>;
  saveSettings(settings: Settings): Promise<Settings>;
  saveAppearance(settings: Settings): Promise<Settings>;
  selectCompanion(modelId: string | null): Promise<Settings>;
  updateCompanion(change: PetPreferenceChange, menuGeneration?: number): Promise<Settings>;
  openCompanionApp(): Promise<void>;
  showCompanionMenu(modelId: string, canAnimate: boolean): Promise<CompanionMenuContext>;
  getCompanionMenuContext(): Promise<CompanionMenuContext>;
  closeCompanionMenu(generation: number): Promise<void>;
  companionMenuReady(generation: number): Promise<void>;
  subscribeCompanionMenuChanged(callback: () => void): Promise<() => void>;
  showCompanion(): Promise<void>;
  hideCompanion(menuGeneration?: number): Promise<void>;
  startCompanionDrag(): Promise<void>;
  getCompanionState(): Promise<{ visible: boolean }>;
  companionReady(modelId: string): Promise<void>;
  subscribeSettingsChanged(callback: () => void): Promise<() => void>;
  subscribeCompanionVisibilityChanged(callback: (visible: boolean) => void): Promise<() => void>;
  subscribeCompanionRecall(callback: () => void): Promise<() => void>;
  callWorker<T>(method: string, params?: Record<string, unknown>): Promise<T>;
  stopWorker(): Promise<void>;
  frontendReady(): Promise<void>;
  listPackages(): Promise<PackageCatalog>;
  importPackage(): Promise<PackageCatalog>;
  selectPackage(id: string, version: string, digest: string): Promise<PackageCatalog>;
  setPackageEnabled(id: string, enabled: boolean): Promise<PackageCatalog>;
  openPackage(id: string): Promise<PackageInstance>;
  openPackageSurface(token: string, options: ModuleSurfaceOptions): Promise<void>;
  getPackageSurfaceContext(): Promise<PackageSurfaceContext>;
  callPackageSurface<T>(generation: string, method: string, params?: Record<string, unknown>): Promise<T>;
  closePackageSurface(generation: string): Promise<void>;
  startPackageSurfaceDrag(generation: string): Promise<void>;
  setPackageSurfaceAlwaysOnTop(generation: string, enabled: boolean): Promise<void>;
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
    saveAppearance: ({ theme, uiStyle, closeToTray }) => request('settings_save_appearance', { theme, uiStyle, closeToTray }),
    selectCompanion: (modelId) => request('companion_select', { modelId }),
    updateCompanion: (change, menuGeneration) => request('companion_update', menuGeneration === undefined ? { change } : { change, menuGeneration }),
    openCompanionApp: () => request('companion_open_main'),
    showCompanionMenu: (modelId, canAnimate) => request('companion_menu_show', { modelId, canAnimate }),
    getCompanionMenuContext: () => request('companion_menu_get_context'),
    closeCompanionMenu: (generation) => request('companion_menu_close', { generation }),
    companionMenuReady: (generation) => request('companion_menu_ready', { generation }),
    subscribeCompanionMenuChanged: (callback) => events ? events('companion-menu-context-changed', callback) : Promise.resolve(() => {}),
    showCompanion: () => request('companion_show'),
    hideCompanion: (menuGeneration) => request('companion_hide', menuGeneration === undefined ? undefined : { menuGeneration }),
    startCompanionDrag: () => request('companion_start_drag'),
    getCompanionState: () => request('companion_get_state'),
    companionReady: (modelId) => request('companion_ready', { modelId }),
    subscribeSettingsChanged: (callback) => events ? events('settings-changed', callback) : Promise.resolve(() => {}),
    subscribeCompanionVisibilityChanged: (callback) => events ? events('companion-visibility-changed', (value) => { if (typeof value === 'boolean') callback(value); }) : Promise.resolve(() => {}),
    subscribeCompanionRecall: (callback) => events ? events('companion-recall', callback) : Promise.resolve(() => {}),
    callWorker: (method, params = {}) => request('worker_call', { method, params }),
    stopWorker: () => request('worker_stop'),
    frontendReady: () => request('frontend_ready'),
    listPackages: () => request('modules_list'),
    importPackage: () => request('modules_import'),
    selectPackage: (id, version, digest) => request('modules_select', { id, version, digest }),
    setPackageEnabled: (id, enabled) => request('modules_enable', { id, enabled }),
    openPackage: (id) => request('modules_open', { id }),
    openPackageSurface: (token, options) => request('module_surface_open', { token, options }),
    getPackageSurfaceContext: () => request('module_surface_context'),
    callPackageSurface: (generation, method, params = {}) => request('module_surface_call', { generation, method, params }),
    closePackageSurface: (generation) => request('module_surface_close', { generation }),
    startPackageSurfaceDrag: (generation) => request('module_surface_start_drag', { generation }),
    setPackageSurfaceAlwaysOnTop: (generation, enabled) => request('module_surface_set_always_on_top', { generation, enabled }),
    listPackageTools: token => request('modules_tools_list', { token }),
    grantPackageTools: (token, tools) => request('modules_tools_grant', { token, tools }),
    callPackageTool: (token, tool, args) => request('modules_tools_call', { token, tool, arguments: args }),
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
