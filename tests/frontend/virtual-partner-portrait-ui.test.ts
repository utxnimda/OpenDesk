import { beforeEach, expect, it, vi } from 'vitest';
import { bubble, mountPage, settle, storedAvatar } from './virtual-partner-page-harness';
import * as avatarFiles from '../../apps/features/virtual_partner/frontend/avatar';
import type { PetPortraitFrame, PetPortraitsClient } from '../../apps/framework/module-sdk/src';

const square = (color = 0) => ({width: 100, height: 100, color} as unknown as HTMLCanvasElement);
const localSource = (count = 1): avatarFiles.AvatarSource => ({
  width: 100, height: 100, frameCount: count, frame: vi.fn(async i => square(i)), close: vi.fn(),
});
beforeEach(() => {
  vi.spyOn(avatarFiles, 'exportAvatarCrop').mockImplementation(() => prepared);
  vi.spyOn(avatarFiles, 'drawAvatarCrop').mockImplementation(() => {});
});

const avatarFile = new File(['image'], 'private-photo.png', {type: 'image/png'});
const prepared = {pixels: 'A'.repeat(49152), preview: 'data:image/png;base64,cHJldmlldw=='};
function selectAvatar(page: Awaited<ReturnType<typeof mountPage>>, file?: File) {
  const input = page.find(el => el.type === 'file');
  input.files = file ? [file] : [];
  input.value = file?.name ?? '';
  const done = input.props.onChange({target: input});
  expect(input.value).toBe('');
  return done;
}

it('previews a local avatar then updates both chat images on save, and discards or saves a reset', async () => {
  const prepare = vi.spyOn(avatarFiles, 'openAvatarSource').mockResolvedValue(localSource());
  const page = await mountPage();
  const images = () => page.root.nodes.filter(el => el.tag === 'img').map(el => el.props.src);
  await page.click('伙伴设置');
  expect(bubble(page.find(el => el.type === 'file'), 'onKeydown', {key: 'Enter'}).defaultPrevented).toBe(false);
  await selectAvatar(page, avatarFile); await selectAvatar(page); await settle();
  expect(prepare).toHaveBeenCalledOnce();
  expect(images()).toEqual([prepared.preview]);
  expect(page.rpc.mock.calls).toEqual([['partner.state', {}]]);

  prepare.mockRejectedValueOnce(new Error('无法解码这张图片'));
  await selectAvatar(page, avatarFile); await settle();
  expect(page.root.label).toContain('无法解码这张图片');
  expect(images()).toEqual([prepared.preview]);
  await page.edit(page.input('伙伴名字'), ' 星河 ');
  await page.click('保存设定');
  expect(page.rpc.mock.calls).toContainEqual(['partner.profile', {name: '星河', personality: '温和', examples: [], avatarPixels: prepared.pixels}]);
  expect(images()).toEqual([storedAvatar, storedAvatar, storedAvatar]);
  await page.click('恢复默认头像');
  expect(images()).toEqual([storedAvatar, storedAvatar]);
  await page.click('关闭'); await page.click('放弃修改并关闭'); await page.click('伙伴设置');
  expect(images()).toEqual([storedAvatar, storedAvatar, storedAvatar]);
  await page.click('恢复默认头像'); await page.click('保存设定');
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.profile').at(-1)?.[1]?.avatarPixels).toBeNull();
  expect(images()).toEqual([]);
  expect(page.root.nodes.filter(el => String(el.props.class).includes('vp-avatar') && el.label === '星')).toHaveLength(3);
});

it('blocks save during decode and ignores results after replacement, reset or discard', async () => {
  let finish!: (image: avatarFiles.AvatarSource) => void;
  const prepare = vi.spyOn(avatarFiles, 'openAvatarSource').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const page = await mountPage();
  await page.click('伙伴设置');
  const first = selectAvatar(page, avatarFile); await settle();
  expect(page.button('保存设定').disabled).toBe(true);
  prepare.mockRejectedValueOnce(new Error('图片格式有误'));
  await selectAvatar(page, avatarFile); await settle();
  finish(localSource()); await first; await settle();
  expect(page.root.label).toContain('图片格式有误');
  expect(page.root.nodes.some(el => el.tag === 'img')).toBe(false);
  const second = selectAvatar(page, avatarFile); await settle();
  await page.click('恢复默认头像'); finish(localSource()); await second; await settle();
  expect(page.button('保存设定').disabled).toBe(false);
  expect(page.root.nodes.some(el => el.tag === 'img')).toBe(false);
  const third = selectAvatar(page, avatarFile); await settle();
  await page.click('关闭'); await page.click('放弃修改并关闭'); finish(localSource()); await third; await settle();
  await page.click('伙伴设置');
  expect(page.root.nodes.some(el => el.tag === 'img')).toBe(false);
  expect(page.rpc.mock.calls).toEqual([['partner.state', {}]]);
});

function petHost(active: string | null) {
  let notify: (() => void) | undefined;
  const sources = [{id: 'idle', label: '待机'}, {id: 'posing', label: '动作'}, {id: 'still', label: '静态'}];
  const pets = ['first', 'second'].map((id, i) => ({id, label: '桌宠 ' + (i + 1), sources}));
  const frame = vi.fn(async (id: string, action: string, index: number): Promise<PetPortraitFrame> => ({
    rgba: new Uint8ClampedArray([id === 'first' ? 1 : 2, action === 'still' ? 0 : 1, index, 255]),
    width: 1, height: 1, frameCount: action === 'still' ? 1 : 4, index,
  }));
  const unsubscribe = vi.fn(() => { notify = undefined; });
  const client: PetPortraitsClient = {
    list: vi.fn(async () => ({activeId: active, pets})), frame,
    onChange: listener => { notify = listener; return unsubscribe; },
  };
  vi.spyOn(avatarFiles, 'avatarFrameFromRgba').mockImplementation(({rgba}) => square(rgba[0] * 100 + rgba[1] * 10 + rgba[2]));
  vi.mocked(avatarFiles.exportAvatarCrop).mockImplementation((image, crop) => {
    const key = [(image as unknown as {color: number}).color, crop.x, crop.y, crop.zoom].join(',');
    return {preview: 'data:image/png;base64,' + btoa(key), pixels: key};
  });
  return {client, frame, unsubscribe, change(id: string | null) { active = id; notify?.(); }};
}
const images = (page: Awaited<ReturnType<typeof mountPage>>) => page.root.nodes.filter(el => el.tag === 'img').map(el => el.props.src);
const picture = (id: number) => 'data:image/png;base64,' + btoa(id + ',0.5,0.5,1');

it('moves and magnifies a square source, changes animation frames without losing framing and submits the displayed crop', async () => {
  const src = localSource(4);
  vi.spyOn(avatarFiles, 'openAvatarSource').mockResolvedValue(src);
  const page = await mountPage();
  await page.click('伙伴设置');
  expect(page.root.nodes.some(el => el.label === '从桌宠选择')).toBe(false);
  await selectAvatar(page, avatarFile); await settle();
  const zoom = page.find(el => el.props['aria-label'] === '头像缩放');
  zoom.props.onInput({target: {value: '2'}}); await settle();
  const view = page.find(el => el.tag === 'canvas');
  view.props.onPointerdown({pointerId: 1, clientX: 20, clientY: 35, button: 0});
  view.props.onPointermove({pointerId: 1, clientX: 69, clientY: 35});
  view.props.onPointerup({type: 'pointerup', pointerId: 1, clientX: 69, clientY: 35});
  await settle();
  expect(vi.mocked(avatarFiles.exportAvatarCrop).mock.lastCall?.[1]).toEqual({x: 0.375, y: 0.5, zoom: 2});
  expect(bubble(view, 'onKeydown', {key: 'ArrowUp'}).defaultPrevented).toBe(true);
  const adjusted = vi.mocked(avatarFiles.exportAvatarCrop).mock.lastCall![1];
  expect(adjusted.y).toBeGreaterThan(0.5);
  await page.click('下一帧');
  const slider = page.find(el => el.props['aria-label'] === '选择动画帧');
  await slider.props.onChange({target: {value: '3'}}); await settle();
  expect(page.button('下一帧').disabled).toBe(true);
  await page.click('上一帧');
  expect(src.frame).toHaveBeenLastCalledWith(2);
  expect(vi.mocked(avatarFiles.exportAvatarCrop).mock.lastCall?.[1]).toEqual(adjusted);
  expect(vi.mocked(avatarFiles.drawAvatarCrop).mock.lastCall?.[2]).toEqual(adjusted);
  await page.click('模型连接'); await page.click('角色设定');
  expect(page.root.label).toContain('3 / 4');
  await page.click('保存设定');
  expect(page.rpc.mock.calls.find(([method]) => method === 'partner.profile')?.[1]?.avatarPixels).toBe(prepared.pixels);
  expect(src.close).toHaveBeenCalledOnce();
});

it('follows current idle frame zero, falls back to the same pet still, and ignores stale responses after disabling pets', async () => {
  const host = petHost('first');
  const page = await mountPage(undefined, {petPortraits: host.client});
  expect(host.frame.mock.calls).toEqual([['first', 'idle', 0]]);
  expect(images(page)).toEqual([picture(110), picture(110)]);
  host.frame.mockRejectedValueOnce(new Error('待机解码失败'));
  host.change('second'); await settle();
  expect(host.frame.mock.calls.slice(-2)).toEqual([['second', 'idle', 0], ['second', 'still', 0]]);
  expect(images(page)).toEqual([picture(200), picture(200)]);
  let delayed!: (frame: PetPortraitFrame) => void;
  host.frame.mockImplementationOnce(() => new Promise(resolve => { delayed = resolve; }));
  host.change('first'); await settle();
  host.change(null); await settle();
  delayed({rgba: new Uint8ClampedArray([1, 1, 0, 255]), width: 1, height: 1, index: 0, frameCount: 4}); await settle();
  expect(images(page)).toEqual([]);
  await page.click('伙伴设置');
  await page.edit(page.input('伙伴名字'), '阿月');
  await page.click('关闭'); await page.click('继续编辑'); await page.click('保存设定');
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.profile').map(([, args]) => args)).toEqual([
    {name: '阿月', personality: '温和', examples: []},
  ]);
});

it('picks any registered pet while disabled, freezes an explicit action frame and resumes following only after a saved reset', async () => {
  const host = petHost(null);
  const page = await mountPage(undefined, {petPortraits: host.client, avatar: storedAvatar});
  expect(host.frame).not.toHaveBeenCalled();
  await page.click('伙伴设置'); await page.click('从桌宠选择');
  const choose = async (label: string, value: string) => { await page.select(label).props.onChange({target: {value}}); await settle(); };
  await choose('头像桌宠', 'second'); await choose('桌宠动作', 'posing'); await page.click('下一帧');
  expect(host.frame).toHaveBeenLastCalledWith('second', 'posing', 1);
  expect(images(page)).toEqual([storedAvatar, storedAvatar, picture(211)]);
  await page.click('保存设定');
  expect(page.rpc.mock.calls.find(([method]) => method === 'partner.profile')?.[1]?.avatarPixels).toBe('211,0.5,0.5,1');
  const calls = host.frame.mock.calls.length;
  host.change('first'); await settle();
  expect(host.frame).toHaveBeenCalledTimes(calls);
  expect(images(page)).toEqual([storedAvatar, storedAvatar, storedAvatar]);
  await page.click('恢复默认头像');
  expect(images(page)).toEqual([storedAvatar, storedAvatar, picture(110)]);
  await page.click('撤销头像修改');
  expect(images(page)).toEqual([storedAvatar, storedAvatar, storedAvatar]);
  await page.click('恢复默认头像'); await page.click('保存设定');
  expect(images(page)).toEqual([picture(110), picture(110), picture(110)]);
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.profile').at(-1)?.[1]?.avatarPixels).toBeNull();
  host.change('second'); await settle();
  expect(images(page)).toEqual([picture(210), picture(210), picture(210)]);
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.profile')).toHaveLength(2);
});

it('preserves the editable crop on a rejected save and displays the committed profile if later conversation refresh fails', async () => {
  vi.spyOn(avatarFiles, 'openAvatarSource').mockResolvedValue(localSource(3));
  const page = await mountPage();
  await page.click('伙伴设置'); await selectAvatar(page, avatarFile); await settle();
  await page.click('下一帧'); await page.edit(page.input('伙伴名字'), '纸鸢');
  page.rpc.mockRejectedValueOnce(new Error('暂时无法保存'));
  await page.click('保存设定');
  expect(page.root.label).toContain('暂时无法保存');
  expect(page.root.label).toContain('2 / 3');
  expect(images(page)).toEqual([prepared.preview]);
  const rpc = page.rpc.getMockImplementation()!;
  page.rpc.mockImplementation((method, args) => method === 'partner.state' ? Promise.reject(new Error('暂时无法刷新话题')) : rpc(method, args));
  await page.click('保存设定');
  expect(page.root.label).toContain('暂时无法刷新话题');
  expect(images(page)).toEqual([storedAvatar, storedAvatar, storedAvatar]);
  expect(page.find(el => el.tag === 'h1').label).toBe('纸鸢');
  expect(page.input('伙伴名字').value).toBe('纸鸢');
});
