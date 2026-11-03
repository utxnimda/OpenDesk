import { expect, it, vi } from 'vitest';
import { baseUrl, bubble, mountPage, settle } from './virtual-partner-page-harness';

it('saves a Chinese partner name with the settings button and restores it when the dialog reopens', async () => {
  const page = await mountPage();
  const heading = page.find(el => el.tag === 'h1');
  expect(heading.label).toBe('小满');
  await page.click('伙伴设置');
  await page.edit(page.input('伙伴名字'), ' 星河 ');
  await page.click('保存设定');
  expect(page.rpc.mock.calls).toEqual([
    ['partner.state', {}],
    ['partner.profile', {name: '星河', personality: '温和', examples: []}],
    ['partner.state', {topicId: 't'}],
  ]);
  expect(page.root.label).toContain('角色设定已保存。');
  expect(heading.label).toBe('星河');
  await page.click('关闭'); await page.click('伙伴设置');
  expect(page.input('伙伴名字').value).toBe('星河');
});

it('reserves Enter for composition and multiline text, then validates before saving from a name input', async () => {
  const page = await mountPage();
  await page.click('伙伴设置');
  const name = page.input('伙伴名字');
  await page.edit(name, '星河');
  const enter = (target: typeof name, fields = {}) => bubble(target, 'onKeydown', {key: 'Enter', ...fields});
  const validity = vi.spyOn(name.form!, 'reportValidity');
  expect(enter(name, {isComposing: true}).defaultPrevented).toBe(false);
  expect(enter(name, {keyCode: 229}).defaultPrevented).toBe(false);
  const personality = page.find(el => el.tag === 'textarea' && el.parent?.label === '性格与说话方式');
  expect(enter(personality).defaultPrevented).toBe(false);
  expect(validity).not.toHaveBeenCalled();

  validity.mockReturnValueOnce(false);
  expect(enter(name).defaultPrevented).toBe(true);
  expect(validity).toHaveBeenCalledOnce();
  expect(page.rpc.mock.calls.map(([method]) => method)).toEqual(['partner.state']);
  expect(enter(name).defaultPrevented).toBe(true);
  await settle();
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.profile')).toEqual([
    ['partner.profile', {name: '星河', personality: '温和', examples: []}],
  ]);
});

it('opens the existing settings panel and restores each account’s selected model and cached choices on A → B → A', async () => {
  const page = await mountPage();
  await page.click('伙伴设置');
  expect(page.rpc.mock.calls.map(([method]) => method)).toEqual(['partner.state']);
  await page.click('模型连接');
  for (const id of ['A', 'B', 'A']) {
    if (page.select('当前 API').value !== id) await page.switchTo(id);
    expect(page.select('当前 API').value).toBe(id);
    const selector = page.select('模型');
    expect(selector.options[selector.selectedIndex].value).toBe('model-' + id);
    expect(selector.options.map(option => option.value)).toEqual(['', 'model-' + id, 'only-' + id, '自定义 ID']);
    expect(page.input('API Key').value).toBe('');
    expect(page.root.nodes.find(el => el.props.class === 'vp-compose-actions')?.label).toContain('model-' + id);
  }
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.connection.select').map(([, p]) => p?.id)).toEqual(['B', 'A']);
  expect(page.rpc.mock.calls.some(([method]) => method === 'partner.models')).toBe(false);
});

it('keeps unsaved fields through tab changes and close, cancels locally, and retries adding with the same UUID', async () => {
  const page = await mountPage();
  await page.open();
  await page.click('新增 API');
  await page.edit(page.input('API 名称'), '备用');
  expect(page.select('当前 API').props.disabled).toBe(true);
  await page.click('角色设定'); await page.click('模型连接');
  expect(page.input('API 名称').value).toBe('备用');
  await page.click('关闭'); await page.click('继续编辑');
  await page.click('取消修改');
  expect(page.select('当前 API').value).toBe('A');
  expect(page.rpc.mock.calls.some(([method]) => method.startsWith('partner.connection.'))).toBe(false);
  await page.click('新增 API');
  await page.edit(page.input('API 名称'), '备用');
  await page.edit(page.input('API 地址'), baseUrl);
  expect(page.button('读取模型').props.disabled).toBe(true);
  await page.edit(page.input('API Key'), 'synthetic');
  await page.click('读取模型');
  expect(page.rpc.mock.calls).toContainEqual(['partner.models', {profileId: null, baseUrl, apiKey: 'synthetic'}]);
  await page.edit(page.select('模型'), 'remote-chat');
  page.rpc.mockRejectedValueOnce(new Error('未收到回执'));
  await page.click('保存连接');
  expect(page.root.label).toContain('未确认设置是否写入');
  expect(page.input('API 名称').value).toBe('备用');
  await page.click('保存连接');
  const writes = page.rpc.mock.calls.filter(([method]) => method === 'partner.connection.upsert').map(([, p]) => p!);
  expect(writes[0].id).toMatch(/^[0-9a-f-]{36}$/);
  expect(writes[0]).toMatchObject({create: true, name: '备用', baseUrl, apiKey: 'synthetic', model: 'remote-chat'});
  expect(writes[1]).toEqual(writes[0]);
  expect(page.select('当前 API').value).toBe(writes[0].id);
  expect(page.input('API Key').value).toBe('');
  expect(page.root.nodes.find(el => el.props.class === 'vp-compose-actions')?.label).toContain('remote-chat');
});

it('ignores late catalog results from the same URL under a different account and requires a key for another endpoint', async () => {
  const page = await mountPage();
  page.catalogs.A = null;
  const normal = page.rpc.getMockImplementation()!;
  let finish!: (result: unknown) => void;
  page.rpc.mockImplementation((method, args) => method === 'partner.models.result'
    ? new Promise(resolve => { finish = resolve; }) : normal(method, args));
  await page.open();
  expect(page.rpc.mock.calls).toContainEqual(['partner.models', {profileId: 'A', baseUrl}]);
  await page.switchTo('B');
  finish({status: 'ready', models: ['only-A']}); await settle();
  expect(page.select('模型').label).toContain('only-B');
  expect(page.select('模型').label).not.toContain('only-A');
  await page.edit(page.input('API Key'), 'second-synthetic');
  expect(page.select('模型').label).not.toContain('only-B');
  await page.edit(page.input('API 地址'), 'https://other.invalid/v1');
  expect(page.input('API Key').value).toBe('');
  expect(page.button('读取模型').props.disabled).toBe(true);
  expect(page.button('保存连接').props.disabled).toBe(true);
});

it('shows the first-run add button and permits manual model entry when the directory is empty', async () => {
  const page = await mountPage([]);
  await page.click('设置模型连接');
  expect(page.select('当前 API').label).toContain('尚未配置 API');
  await page.click('新增 API');
  await page.edit(page.input('API 名称'), '第一个 API');
  await page.edit(page.input('API Key'), 'synthetic');
  await page.edit(page.select('模型'), '自定义 ID');
  await page.edit(page.input('自定义模型 ID'), 'private-deployment');
  expect(page.rpc.mock.calls.some(([method]) => method === 'partner.models')).toBe(false);
  const normal = page.rpc.getMockImplementation()!;
  page.rpc.mockImplementationOnce(async (method, args) => {
    page.catalogs[String(args?.id)] = [];
    return normal(method, args);
  });
  await page.click('保存连接');
  expect(page.select('模型').label).toContain('private-deployment（已保存）');
  expect(page.button('刷新模型').props.disabled).toBe(false);
  await page.click('角色设定'); await page.click('模型连接');
  expect(page.root.label).toContain('服务未返回模型');
  expect(page.rpc.mock.calls.some(([method]) => method === 'partner.models')).toBe(false);
  await page.click('刷新模型');
  expect(page.rpc.mock.calls).toContainEqual(['partner.models', {profileId: page.select('当前 API').value, baseUrl: 'https://api.openai.com/v1'}]);
});

it('does not start a remote request when a pending local catalog read resolves after closing the model panel', async () => {
  const page = await mountPage();
  const normal = page.rpc.getMockImplementation()!;
  let finish!: (result: unknown) => void;
  page.rpc.mockImplementation((method, args) => method === 'partner.catalog'
    ? new Promise(resolve => { finish = resolve; }) : normal(method, args));
  await page.open();
  expect(page.select('模型').label).toContain('model-A（已保存）');
  await page.click('关闭');
  finish({models: null}); await settle();
  expect(page.rpc.mock.calls.some(([method]) => method === 'partner.models')).toBe(false);
});

it('fills an uncached catalog after saving a new API while retaining its manually chosen model', async () => {
  const page = await mountPage([]);
  await page.click('设置模型连接'); await page.click('新增 API');
  await page.edit(page.input('API 名称'), '新服务');
  await page.edit(page.input('API Key'), 'synthetic');
  await page.edit(page.select('模型'), '自定义 ID');
  await page.edit(page.input('自定义模型 ID'), 'manual-id');
  await page.click('保存连接');
  expect(page.rpc.mock.calls.filter(([method]) => method === 'partner.models')).toEqual([['partner.models',
    {profileId: page.select('当前 API').value, baseUrl: 'https://api.openai.com/v1'}]]);
  const selector = page.select('模型');
  expect(selector.options[selector.selectedIndex].value).toBe('manual-id');
  expect(selector.label).toContain('remote-chat');
});
