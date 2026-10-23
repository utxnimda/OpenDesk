import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString, type SSRContext } from '@vue/server-renderer';
import { compileStyle, parse } from '@vue/compiler-sfc';
import CompanionMenuPanel from '../../apps/framework/frontend/src/companions/CompanionMenuPanel.vue';
import BrowserCompanionMenu from '../../apps/framework/frontend/src/companions/BrowserCompanionMenu.vue';
import type { CompanionModel } from '../../apps/framework/frontend/src/companions/contracts';
import { clonePersonalization, type PetSettings } from '../../apps/framework/frontend/src/core/personalization';

const simple: CompanionModel = { id: 'simple', label: '简单角色', description: '', assets: { idle: '/loop.png', still: '/still.png' } };
const thinking: CompanionModel = { ...simple, id: 'thinking', label: '思考角色', assets: { ...simple.assets, thinking: '/thinking.webm' }, actionDurationsMs: { thinking: 6042 }, actionMediaTypes: { thinking: 'video' } };
const pet = (values: Partial<PetSettings> = {}): PetSettings => ({ ...clonePersonalization().pet, enabled: true, mode: 'desktop', modelId: 'thinking', ...values });
const choiceTag = (html: string, choice: string) => html.match(new RegExp(`<button\\b[^>]*data-choice="${choice}"[^>]*>`))?.[0];

describe('shared companion menu panel', () => {
  it('shows only real actions and focuses the effective fallback after a model change', async () => {
    const rich = await renderToString(createSSRApp(CompanionMenuPanel, { model: thinking, settings: pet({ actionMode: 'thinking' }), canAnimate: true }));
    expect(rich).toContain('role="dialog" aria-label="思考角色的宠物菜单"');
    expect(rich).toContain('src="/still.png"');
    expect(choiceTag(rich, 'action:thinking')).toContain('aria-checked="true" tabindex="0"');
    expect(choiceTag(rich, 'action:idle')).toContain('aria-checked="false" tabindex="-1"');
    for (const action of ['wave', 'walk', 'sleep']) expect(choiceTag(rich, `action:${action}`)).toBeUndefined();
    const changed = await renderToString(createSSRApp(CompanionMenuPanel, { model: simple, settings: pet({ actionMode: 'thinking' }), canAnimate: true }));
    expect(choiceTag(changed, 'action:thinking')).toBeUndefined();
    expect(choiceTag(changed, 'action:idle')).toContain('aria-checked="true" tabindex="0"');
  });

  it('offers resume while paused and disables playback when animation is unavailable', async () => {
    const props = { model: simple, settings: pet({ animated: false }), canAnimate: true };
    const paused = await renderToString(createSSRApp(CompanionMenuPanel, props));
    expect(paused).toContain('动画已暂停');
    expect(paused).toContain('继续动画');
    expect(choiceTag(paused, 'toggle-animation')).not.toContain(' disabled');
    const staticOnly = await renderToString(createSSRApp(CompanionMenuPanel, { ...props, canAnimate: false }));
    expect(staticOnly).toContain('静态陪伴');
    expect(choiceTag(staticOnly, 'toggle-animation')).toContain(' disabled');
  });

  it('keeps desktop-only controls unavailable in browser preview without hiding in-app choices', async () => {
    const html = await renderToString(createSSRApp(CompanionMenuPanel, { model: thinking, settings: pet({ mode: 'in-app' }), canAnimate: true, desktopAvailable: false }));
    expect(choiceTag(html, 'mode:desktop')).toContain(' disabled');
    expect(choiceTag(html, 'open-app')).toContain(' disabled');
    expect(choiceTag(html, 'mode:in-app')).toContain('aria-checked="true" tabindex="0"');
    expect(choiceTag(html, 'toggle-top')).toBeUndefined();
    expect(choiceTag(html, 'hide')).not.toContain(' disabled');
    const desktop = await renderToString(createSSRApp(CompanionMenuPanel, { model: thinking, settings: pet({ alwaysOnTop: true }), canAnimate: true }));
    expect(choiceTag(desktop, 'toggle-top')).toContain('role="switch"');
    expect(choiceTag(desktop, 'toggle-top')).toContain('aria-checked="true"');
  });

  it('exposes five bounded sizes and leaves close available while an update is in flight', async () => {
    const html = await renderToString(createSSRApp(CompanionMenuPanel, { model: thinking, settings: pet({ size: 200 }), canAnimate: true, busy: true, error: '无法保存 <设置>，请重试' }));
    expect(html).toContain('aria-busy="true"');
    expect(html).toContain('正在更新…');
    expect(html).toMatch(/role="alert"[^>]*>无法保存 &lt;设置&gt;，请重试/);
    const choices = [...html.matchAll(/<button\b[^>]*data-choice="([^"]+)"[^>]*>/g)];
    expect(choices.filter((match) => match[1].startsWith('size:')).map((match) => match[1])).toEqual(['size:96', 'size:128', 'size:160', 'size:200', 'size:256']);
    expect(choiceTag(html, 'size:200')).toContain('aria-checked="true"');
    for (const [tag] of choices) expect(tag).toContain(' disabled');
    const close = html.match(/<button\b[^>]*aria-label="关闭宠物菜单"[^>]*>/)?.[0];
    expect(close).toBeDefined();
    expect(close).not.toContain(' disabled');
  });

  it('renders the same panel through the teleport wrapper without accessing browser globals during SSR', async () => {
    const context: SSRContext = {};
    await renderToString(createSSRApp(BrowserCompanionMenu, { model: thinking, settings: pet(), canAnimate: true, position: { x: 30, y: 50 }, error: '示例错误' }), context);
    const overlay = context.teleports?.body ?? '';
    expect(overlay).toContain('class="pet-menu-position"');
    expect(overlay).toContain('left:30px;top:50px');
    expect(overlay).toContain('思考角色的宠物菜单');
    expect(overlay).toMatch(/role="alert"[^>]*>示例错误/);
    expect(overlay).not.toContain('<details');
  });

  it('compiles dark palettes onto the menu panel rather than changing root variables for the host', () => {
    const file = new URL('../../apps/framework/frontend/src/companions/CompanionMenuPanel.vue', import.meta.url);
    const descriptor = parse(readFileSync(file, 'utf8')).descriptor;
    const css = compileStyle({ source: descriptor.styles[0].content, filename: file.pathname, id: 'data-v-menu-test', scoped: true });
    expect(css.errors).toEqual([]);
    expect(css.code).toContain(':root[data-theme="dark"] .pet-menu-panel[data-v-menu-test]');
    expect(css.code).toContain(':root[data-theme="graphite"] .pet-menu-panel[data-v-menu-test]');
    expect(css.code).not.toMatch(/:root\[data-theme="(?:dark|graphite)"\]\s*\{/);
  });
});
