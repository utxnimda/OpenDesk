import { describe, expect, it } from 'vitest';
import { createSSRApp } from 'vue';
import { renderToString } from '@vue/server-renderer';
import CompanionPet from '../../apps/framework/frontend/src/companions/CompanionPet.vue';
import { companionSelectedAction, type CompanionModel } from '../../apps/framework/frontend/src/companions/contracts';

const model: CompanionModel = { id: 'fixture', label: 'Fixture', description: 'Local test fixture', assets: { idle: '/idle.png', still: '/still.png', thinking: '/thinking.webm', sleep: '/sleep.png' }, actionDurationsMs: { thinking: 6042 }, actionMediaTypes: { thinking: 'video' }, primaryAction: 'thinking' };
const toggle = (html: string, name: string) => html.match(new RegExp(`<button\\b[^>]*aria-label="${name}"[^>]*>`))?.[0];

describe('companion controls and selected mode', () => {
  it('allows continuing a persistently paused pet and leaves only the toggle and menu shortcuts', async () => {
    const html = await renderToString(createSSRApp(CompanionPet, { model, size: 160, animated: false, actionMode: 'thinking' }));
    expect(toggle(html, '继续宠物动画')).toBeDefined();
    expect(toggle(html, '继续宠物动画')).not.toContain('disabled');
    expect(html).toContain('aria-label="宠物菜单"');
    expect(html).not.toContain('aria-label="托腮思考"');
    expect(html).not.toContain('aria-label="暂时收起宠物"');
    expect(html).toContain('data-action="still"');
    expect(html).toContain('src="/still.png"');
    expect(html).not.toContain('<video');
  });

  it.each([{ active: false }, { reducedMotion: true }])('disables playback for unavailable animation while keeping the menu: %j', async (restriction) => {
    const html = await renderToString(createSSRApp(CompanionPet, { model, size: 160, animated: false, ...restriction }));
    expect(toggle(html, '继续宠物动画')).toContain('disabled');
    expect(toggle(html, '宠物菜单')).not.toContain('disabled');
    expect(html).toContain('data-action="still"');
  });

  it('starts the selected thinking mode and keeps its actual video as the media source', async () => {
    const html = await renderToString(createSSRApp(CompanionPet, { model, size: 160, animated: true, actionMode: 'thinking' }));
    expect(html).toContain('data-action="thinking"');
    expect(html).toContain('<video');
    expect(html).toContain('src="/thinking.webm"');
    expect(html).toContain('点击托腮思考');
    expect(html).not.toContain('src="/idle.png"');
  });

  it('falls back for missing model resources but preserves the explicitly selected available mode', async () => {
    const limited = { ...model, assets: { idle: '/idle.png', still: '/still.png' } };
    expect(companionSelectedAction(limited, 'thinking')).toBe('idle');
    expect(companionSelectedAction(model, 'sleep')).toBe('sleep');
    const fallback = await renderToString(createSSRApp(CompanionPet, { model: limited, size: 160, animated: true, actionMode: 'thinking' }));
    expect(fallback).toContain('data-action="idle"');
    expect(fallback).toContain('src="/idle.png"');
    const sleep = await renderToString(createSSRApp(CompanionPet, { model, size: 160, animated: true, actionMode: 'sleep' }));
    expect(sleep).toContain('data-action="sleep"');
    expect(sleep).toContain('src="/sleep.png"');
  });
});
