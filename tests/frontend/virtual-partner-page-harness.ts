import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc';
import ts from 'typescript';
import * as Vue from 'vue';
import { afterEach, beforeAll, expect, vi } from 'vitest';
import PartnerPage from '../../apps/features/virtual_partner/frontend/PartnerPage.vue';
import MemoryBook from '../../apps/features/virtual_partner/frontend/MemoryBook.vue';
import OutingMemories from '../../apps/features/virtual_partner/frontend/OutingMemories.vue';
import PartnerReply from '../../apps/features/virtual_partner/frontend/PartnerReply.vue';
import PartnerAvatar from '../../apps/features/virtual_partner/frontend/PartnerAvatar.vue';
import PartnerAvatarEditor from '../../apps/features/virtual_partner/frontend/PartnerAvatarEditor.vue';
import PartnerDialog from '../../apps/features/virtual_partner/frontend/PartnerDialog.vue';
import PartnerForm from '../../apps/features/virtual_partner/frontend/PartnerForm.vue';
import PartnerSettings from '../../apps/features/virtual_partner/frontend/PartnerSettings.vue';
import PartnerConnections from '../../apps/features/virtual_partner/frontend/PartnerConnections.vue';
import PartnerOuting from '../../apps/features/virtual_partner/frontend/PartnerOuting.vue';
import OutingSchedule from '../../apps/features/virtual_partner/frontend/OutingSchedule.vue';
import OutingClock from '../../apps/features/virtual_partner/frontend/OutingClock.vue';
import type { OutingScheduleState } from '../../apps/features/virtual_partner/frontend/outingClock';
import type { ConnectionProfile, Persona, Snapshot } from '../../apps/features/virtual_partner/frontend/types';
import type { LimitedHost } from '../../apps/framework/module-sdk/src';

// Node Vitest loads the SSR version of an SFC. Render its actual client template
// on an in-memory host so the tests can operate the mounted page's controls.
beforeAll(() => {
  for (const [name, component] of Object.entries({PartnerPage, PartnerReply, PartnerAvatar, PartnerAvatarEditor, PartnerDialog, PartnerForm, PartnerSettings, PartnerConnections, PartnerOuting, OutingSchedule, OutingClock, MemoryBook, OutingMemories})) {
    const filename = resolve('apps/features/virtual_partner/frontend', name + '.vue');
    const { descriptor } = parse(readFileSync(filename, 'utf8'), {filename});
    const bindings = compileScript(descriptor, {id: name}).bindings;
    const { code, errors } = compileTemplate({source: descriptor.template!.content, filename, id: name,
      compilerOptions: {bindingMetadata: bindings, hoistStatic: false}});
    if (errors.length) throw new Error(errors.join('\n'));
    const js = ts.transpileModule(code, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}});
    const exports = {};
    new Function('require', 'exports', js.outputText)((id: string) => {
      if (id !== 'vue') throw new Error(id);
      return Vue;
    }, exports);
    Object.assign(component, exports);
  }
});

class Element {
  children: Element[] = [];
  parent: Element | null = null;
  props: Record<string, any> = {};
  style = {display: ''};
  value = '';
  files?: File[];
  selectedIndex = -1;
  constructor(readonly tag: string, public text = '') {}
  get options() { return this.children.filter(el => el.tag === 'option'); }
  get label(): string { return this.text + this.children.map(child => child.label).join(''); }
  get nodes(): Element[] { return [this, ...this.children.flatMap(child => child.nodes)]; }
  get tagName() { return this.tag.toUpperCase(); }
  get type() { return this.props.type ?? (this.tag === 'button' ? 'submit' : 'text'); }
  get disabled() { return Boolean(this.props.disabled); }
  get form() { return this.closest('form'); }
  closest(tag: string): Element | null {
    for (let node: Element | null = this; node; node = node.parent) {
      if (node.tag === tag) return node;
    }
    return null;
  }
  querySelector(selector: string) {
    if (selector !== 'button[type="submit"]') throw new Error('Unsupported selector ' + selector);
    return this.nodes.find(el => el.tag === 'button' && el.props.type === 'submit') ?? null;
  }
  reportValidity() { return true; }
  focus() {}
  select() {}
  click() { if (!this.disabled) bubble(this, 'onClick'); }
  getBoundingClientRect() { return {width: 196}; }
  setPointerCapture() {}
  getRootNode() { return null; }
  showModal() {}
  addEventListener() {}
  removeEventListener() {}
}
const renderer = Vue.createRenderer<Element, Element>({
  createElement: tag => new Element(tag), createText: text => new Element('#text', text), createComment: () => new Element('#comment'),
  setText: (el, text) => { el.text = text; }, setElementText: (el, text) => { el.text = text; el.children = []; },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
  patchProp(el, key, _old, value) { el.props[key] = value; if (key === 'value') el.value = value; },
  insert(el, parent, anchor) {
    if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1);
    el.parent = parent;
    parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, el);
  },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); el.parent = null; },
});
export const baseUrl = 'https://account.invalid/v1';
export const storedAvatar = 'data:image/png;base64,c3RvcmVk';
const profile = (id: string): ConnectionProfile => ({id, name: '账号 ' + id, baseUrl, model: 'model-' + id, hasKey: true});
export const settle = async () => { await new Promise<void>(resolve => setImmediate(resolve)); await Vue.nextTick(); };

// API 1 allows click and keydown to bubble, but sandboxed forms never submit.
export function bubble(target: Element, handler: 'onClick' | 'onKeydown', fields: Record<string, unknown> = {}) {
  const event = {target, currentTarget: target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields};
  for (let el: Element | null = target; el; el = el.parent) {
    event.currentTarget = el;
    el.props[handler]?.(event);
  }
  return event;
}

const mounted: Vue.App[] = [];
afterEach(() => { mounted.splice(0).forEach(app => app.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

export async function mountPage(initial: ConnectionProfile[] = [profile('A'), profile('B')], options: {
  petPortraits?: LimitedHost['petPortraits']; avatar?: string;
  outingSchedule?: OutingScheduleState;
  outingWorker?: (method: string, args: Record<string, unknown>) => unknown;
  chat?: () => Pick<Snapshot, 'messages' | 'run'>;
} = {}) {
  vi.stubGlobal('Document', class {});
  vi.stubGlobal('ShadowRoot', class {});
  if (options.outingSchedule) vi.stubGlobal('document', new EventTarget());
  const profiles = [...initial];
  let activeId = profiles[0]?.id ?? null;
  const catalogs: Record<string, string[] | null> = {A: ['model-A', 'only-A'], B: ['model-B', 'only-B']};
  let persona: Persona = {id: 'p', name: '小满', personality: '温和', avatar: options.avatar};
  const snapshot = () => ({
    persona: {...persona}, currentTopicId: 't', topics: [], messages: [], memories: [], run: null, outingSchedule: options.outingSchedule,
    connection: profiles.find(p => p.id === activeId) ?? {baseUrl, hasKey: false, model: ''},
    ...options.chat?.(),
  });
  const rpc = vi.fn(async (method: string, args: Record<string, unknown> = {}): Promise<unknown> => {
    if (method.startsWith('partner.outing.') && options.outingWorker) return options.outingWorker(method, args);
    switch (method) {
      case 'partner.state': return snapshot();
      case 'partner.run': return options.chat?.().run;
      case 'partner.profile': {
        const {avatarPixels, ...fields} = args;
        persona = {...persona, ...fields};
        if (avatarPixels !== undefined) persona.avatar = avatarPixels === null ? null : storedAvatar;
        return persona;
      }
      case 'partner.connections': return {activeId, profiles: profiles.map(p => ({...p}))};
      case 'partner.catalog': return {models: catalogs[String(args.id)] ?? null};
      case 'partner.connection.select': activeId = String(args.id); return {activeId, profiles};
      case 'partner.connection.upsert': {
        const id = String(args.id);
        const p = {id, name: String(args.name), model: String(args.model), baseUrl: String(args.baseUrl), hasKey: true};
        const index = profiles.findIndex(p => p.id === id);
        if (index < 0) profiles.push(p); else profiles[index] = p;
        activeId = id;
        return {activeId, profiles};
      }
      case 'partner.models': return {id: 'lookup'};
      case 'partner.models.result': return {status: 'ready', models: ['remote-chat']};
      default: throw new Error(method);
    }
  });
  const root = new Element('root');
  const app = renderer.createApp(PartnerPage, {host: {available: true, callWorker: rpc, petPortraits: options.petPortraits} as LimitedHost});
  app.provide(Vue.ssrContextKey, {modules: new Set()});
  app.mount(root);
  mounted.push(app);
  await settle();
  const find = (predicate: (el: Element) => boolean) => {
    const el = root.nodes.find(predicate);
    if (!el) throw new Error('Control not found in ' + root.label);
    return el;
  };
  const button = (label: string) => find(el => el.tag === 'button' && (el.label === label || el.props['aria-label'] === label));
  const click = async (label: string) => {
    const el = button(label);
    expect(el.props.disabled).toBeFalsy();
    el.click(); await settle();
  };
  const select = (label: string) => find(el => el.tag === 'select' && el.props['aria-label'] === label);
  const input = (label: string) => find(el => el.tag === 'input' && el.parent?.label === label);
  const edit = async (el: Element, value: string) => {
    expect(el.props.disabled).toBeFalsy();
    el.props['onUpdate:modelValue'](value); await settle();
  };
  const switchTo = async (id: string) => {
    const el = select('当前 API');
    expect(el.props.disabled).toBeFalsy();
    el.value = id; el.props.onChange({target: el}); await settle();
  };
  return {app, click, button, root, select, input, edit, switchTo, find, catalogs, rpc,
    open: async () => { await click('伙伴设置'); await click('模型连接'); }};
}
