const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {pathToFileURL} = require('node:url');
let installTowerCapture;
test.before(async () => {
  ({installTowerCapture} = await import(pathToFileURL(path.resolve(__dirname, '../../apps/features/tower_assistant/capture/observer.js')).href));
});
const workIds = [382847177923468, 890260672342582, 481758359077511, 976952201246496,
  262198584001088, 852732572548979, 696683569195655, 218834594110811, 217737310598270];

function fixture({missingAction = false, invalidNode = false, nameAvailable = true, name = '甲', profile = 'audited-2026-10-25'} = {}) {
  const messages = [];
  const calls = [];
  let heartbeat;
  const scope = {console, setInterval: fn => { heartbeat = fn; }};
  installTowerCapture(scope, record => messages.push(record), profile);
  const expression = profile === 'audited-2026-10-25-r2' ? 5584 : 5579;
  const workExpressions = profile === 'audited-2026-10-25-r2' ? [5620, 5621, 5622, 5623] : [5615, 5616, 5617, 5618];
  const workParameters = [7, 12, 1000000, 1];
  const damageParameters = [7, 5, 42, 0, 0, 1];
  const inputs = [1000000, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1];
  let hp = 10000;
  let special = false;
  let failAction = null;
  let loaded = false;
  let workBranch = 1;
  let execution = '伐木';
  let skillKind = '普攻';
  let damageType = 1;
  let workThrows = null;
  const workInputs = [1, 123.5, 2, .2, .3, .4, 1.1, 1.2, 1.3, 1.4, 20];
  const flatBonuses = {65: 12.75, 66: -3.25, 67: 8.9};
  let metadataAvailable = true;
  const metadataRows = new Map([
    ['save:7:5:2', 'B6'], ['save:7:6:2', 4], ['save:7:9:2', '自动'], ['save:7:16:2', 11],
    ['save:11:0:9', 1], ['save:11:1:9', 20], ['save:11:7:9', 7], ['save:11:5:9', 4], ['save:11:6:9', 8],
    ['zy:4:0:0', 4], ['zy:4:1:0', '测试职业'], ['zy:4:5:0', '测试职业说明'],
    ['wp:20:0:0', 20], ['wp:20:1:0', '测试武器'], ['wp:20:2:0', '测试图标'], ['wp:20:6:0', 1], ['wp:20:7:0', '刀'],
  ]);
  const actions = new Map();
  const instance = {GetObjectClass: () => ({GetName: () => '敌人核心'}), GetInstanceVariableValue: () => hp};
  const expressions = Array(5624);
  const workEvaluators = [];
  for (const [branch, index] of workExpressions.entries()) {
    expressions[index] = () => function(...args) {
      calls.push(['work-expression', this, args]);
      if (workThrows) throw workThrows;
      const v = workInputs;
      return branch === 0
        ? Math.ceil(Math.ceil(v[0] * v[1] * v[2] * (1 + v[3] + v[4] + v[5]) * (1 + v[6]) * v[7] * v[8] * v[9]) * ((100 + v[10]) / 100) + Math.floor(flatBonuses[67]))
        : Math.ceil(v[0] * v[1] * v[2] * (1 + v[3] + v[4]) * (1 + v[5]) * v[6] * v[7] * v[8] * v[9]) + Math.floor(flatBonuses[64 + branch]);
    };
  }
  expressions[expression] = function(parameter) {
    assert.equal(parameter.marker, 'parameter');
    return function(...args) {
      calls.push(['expression', this, args]);
      if (parameter.throw) throw parameter.throw;
      return 1000000;
    };
  };
  scope.C3_ExpressionFuncs = expressions;
  for (const [branch, index] of workExpressions.entries()) {
    const count = branch === 0 ? 11 : 10;
    workEvaluators.push(expressions[index]({_GetNode: index => ({GetVar: () => ({GetValue: () => index === count + 1 ? 7 : workInputs[index]})})}));
  }
  const parameter = {marker: 'parameter', _GetNode: index => {
    if (invalidNode && index === 0) throw new Error('missing-input');
    return {GetVar: () => ({GetValue: () => inputs[index]})};
  }};
  const evaluate = expressions[expression](parameter);
  class EventBlock {
    GetScopeParent() { return {GetFunctionName: () => '战斗伤害计算'}; }
    RunAsExpressionFunctionCall(...args) {
      calls.push(['calculation', this, args]);
      return special ? Math.ceil(hp) : evaluate.call(block, 'evaluate-argument');
    }
  }
  scope.C3 = {EventBlock};
  const block = new EventBlock();
  let boundCalculation;
  const manager = {
    GetActionBySID: id => loaded ? actions.get(id) : null,
    GetFunctionBlockByName: name => name === '战斗伤害计算' ? {GetEventBlock: () => block, GetReturnType: () => 1, IsAsync: () => false} :
      {CaptureFunctionParameters: () => (name === '采集核心碰撞' ? workParameters : damageParameters).slice()},
  };
  for (const id of [...workIds, 308069267939259, 555212428242208, 302775837391885, 423236544410898, 994019443997195]) {
    actions.set(id, {Run(...args) {
      calls.push([id, this, args]);
      if (failAction === id) throw new Error('original-action-failure');
      if (id === 302775837391885) damageParameters[4] = Math.min(boundCalculation(), hp);
      if (id === 308069267939259 || id === 555212428242208) workParameters[2] = workEvaluators[workBranch].call(this, 'work-evaluation');
      if (id === 423236544410898 || id === 994019443997195) hp -= damageParameters[4];
      return `return:${id}`;
    }});
  }
  if (missingAction) actions.delete(workIds[0]);
  const runtime = {
    GetEventSheetManager: () => manager,
    GetGameTime: () => 123.5,
    GetInstanceByUID: uid => uid === 42 ? instance : null,
    GetObjectClassByName: object => {
      if (object === '角色核心') return metadataAvailable ? {GetInstances: () => [{GetInstanceVariableValue: index => index === 0 ? 7 : index === 6 ? execution : 0}]} : null;
      if (!nameAvailable) return null;
      return {GetInstances: () => [{GetSdkInstance: () => ({At: (x, y, z = 0) => {
        if (object === 'save' && x === 7 && y === 1 && z === 2) return name;
        if (!metadataAvailable) return undefined;
        if (object === '属性临时') return y === 7 ? flatBonuses[x] ?? (x >= 1 && x <= 7 ? x * 10 : undefined) : undefined;
        if (object === 'jn' && x === 5) return ({1: '测试攻击', 3: skillKind, 8: damageType})[y];
        return metadataRows.get(`${object}:${x}:${y}:${z}`);
      }})}]};
    },
    _LoadDataJson(...args) {
      calls.push(['load', this, args]);
      // Real Construct captures a bound PROTOTYPE method while loading nodes.
      boundCalculation = scope.C3.EventBlock.prototype.RunAsExpressionFunctionCall.bind(block, 'calculation-argument');
      return Promise.resolve().then(() => { loaded = true; });
    },
    async Init() {
      await this._LoadDataJson('data');
      // A real first work event immediately when Init's await resumes.
      if (!missingAction) actions.get(workIds[0]).Run('first-tick');
      return 'init-result';
    },
  };
  scope.C3_CreateRuntime = function(...args) { calls.push(['create', this, args]); return runtime; };
  scope.C3_InitRuntime = (value, options) => value.Init(options);
  const receiver = {};
  assert.equal(scope.C3_CreateRuntime.call(receiver, 'options'), runtime);
  return {scope, runtime, actions, messages, calls, parameter, inputs, block, workParameters, damageParameters,
    heartbeat: () => heartbeat(), setHp: value => { hp = value; }, setSpecial: () => { special = true; },
    setFailAction: value => { failAction = value; }, hp: () => hp,
    workExpressions, workInputs, flatBonuses, metadataRows,
    evaluateWork: () => workEvaluators[workBranch](),
    setWorkBranch: value => { workBranch = value; }, setWorkThrows: value => { workThrows = value; },
    setExecution: value => { execution = value; }, setMetadataAvailable: value => { metadataAvailable = value; },
    setSkill: (kind, type) => { skillKind = kind; damageType = type; }};
}
const events = f => f.messages.filter(message => message.type === 'event').map(message => message.event);

test('async data loading installs all actions before the first resumed game tick', async () => {
  const f = fixture();
  const load = f.runtime._LoadDataJson;
  const promise = f.scope.C3_InitRuntime(f.runtime, {});
  assert.ok(promise instanceof Promise);
  assert.equal(await promise, 'init-result');
  assert.equal(events(f).length, 1);
  assert.equal(events(f)[0].amount, 1000000);
  assert.equal(events(f)[0].personName, '甲');
  assert.equal(f.messages[0].type, 'ready');
  assert.equal(f.calls.find(call => call[0] === 'load')[1], f.runtime);
  assert.equal(typeof load, 'function');
});

test('all nine consumption actions emit once with complete output, preserving receiver arguments and return', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  for (const id of workIds) {
    const receiver = {id};
    assert.equal(f.actions.get(id).Run.call(receiver, 1, 'arg'), `return:${id}`);
    const actual = f.calls.at(-1);
    assert.equal(actual[1], receiver); assert.deepEqual(actual[2], [1, 'arg']);
  }
  assert.equal(events(f).length, 9);
  assert.equal(events(f).reduce((sum, row) => sum + row.amount, 0), 9000000);
  assert.equal(events(f).filter(row => row.category === '城墙修复').length, 1);
  assert.ok(events(f).every(row => !Object.hasOwn(row, 'effectiveAmount') && !Object.hasOwn(row, 'formula')));
  assert.deepEqual(events(f).map(row => row.sequence), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
});

test('main damage captures all fourteen inputs and full amount separately from capped HP', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  // A preview calculation must not create a hit.
  f.block.RunAsExpressionFunctionCall('preview');
  assert.equal(events(f).length, 0);
  f.actions.get(302775837391885).Run();
  f.actions.get(423236544410898).Run();
  const row = events(f)[0];
  assert.equal(row.amount, 1000000); assert.equal(row.effectiveAmount, 10000);
  assert.equal(row.formulaCoverage, 'main-product');
  assert.deepEqual(Array.from(row.formula.inputs), f.inputs);
  assert.equal(row.formula.inputs[0], 1000000); assert.equal(row.formula.result, row.amount);
  assert.equal(f.calls.filter(call => call[0] === 'expression').length, 2);
  assert.equal(f.hp(), 0);
});

test('special and stored buff damage do not invent a main formula', async () => {
  const f = fixture({nameAvailable: false}); await f.runtime._LoadDataJson();
  f.setSpecial(); f.setHp(12.3);
  f.actions.get(302775837391885).Run(); f.actions.get(423236544410898).Run();
  let row = events(f)[0];
  assert.equal(row.amount, 13); assert.equal(row.effectiveAmount, 12.3);
  assert.equal(row.formulaCoverage, 'special-return'); assert.equal(row.formula, undefined);
  assert.equal(row.personName, '人物 #7');
  f.setHp(5); f.damageParameters[4] = 100;
  f.actions.get(994019443997195).Run();
  row = events(f)[1];
  assert.equal(row.amount, 100); assert.equal(row.effectiveAmount, 5);
  assert.equal(row.formulaCoverage, 'stored-buff-value-only'); assert.equal(row.formula, undefined);
});

test('original action and expression exceptions propagate without emitting success', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  f.setFailAction(workIds[0]);
  assert.throws(() => f.actions.get(workIds[0]).Run(), /original-action-failure/);
  assert.equal(events(f).length, 0);
  const expected = new Error('original-expression-failure'); f.parameter.throw = expected;
  assert.throws(() => f.actions.get(302775837391885).Run(), error => error === expected);
  assert.equal(events(f).length, 0);
});

test('an observation failure marks the stream incomplete but leaves game damage unchanged', async () => {
  const f = fixture({invalidNode: true}); await f.runtime._LoadDataJson();
  f.actions.get(302775837391885).Run(); f.actions.get(423236544410898).Run();
  assert.equal(f.hp(), 0);
  assert.ok(f.messages.some(message => message.type === 'error'));
  f.heartbeat(); assert.equal(f.messages.at(-1).type, 'error');
});

test('unknown action mapping reports failure and leaves all original actions intact', async () => {
  const f = fixture({missingAction: true});
  const before = f.actions.get(workIds[1]).Run;
  await f.runtime._LoadDataJson();
  assert.equal(f.actions.get(workIds[1]).Run, before);
  assert.ok(f.messages.some(message => message.type === 'error'));
  assert.equal(f.messages.some(message => message.type === 'ready'), false);
});

test('the data-load Promise object and rejection are preserved', async () => {
  const scope = {console, setInterval() {}};
  const messages = []; installTowerCapture(scope, m => messages.push(m));
  const error = new Error('load-failure');
  const promise = Promise.reject(error);
  const runtime = {_LoadDataJson() { return promise; }};
  scope.C3 = {EventBlock: class { RunAsExpressionFunctionCall() {} }};
  scope.C3_CreateRuntime = () => runtime; scope.C3_CreateRuntime();
  assert.equal(runtime._LoadDataJson(), promise);
  await assert.rejects(promise, error);
  assert.equal(messages.at(-1).type, 'error');
});

test('names are bounded and control characters do not invalidate the event protocol', async () => {
  const f = fixture({name: '\n\u0000' + '甲'.repeat(150) + '\t'}); await f.runtime._LoadDataJson();
  f.actions.get(workIds[0]).Run();
  assert.equal(events(f)[0].personName, '甲'.repeat(100));
  const astral = fixture({name: '🦊'.repeat(150)}); await astral.runtime._LoadDataJson();
  astral.actions.get(workIds[0]).Run();
  assert.equal(events(astral)[0].personName, '🦊'.repeat(100));
  const blank = fixture({name: '\n\t\u0000'}); await blank.runtime._LoadDataJson();
  blank.actions.get(workIds[0]).Run();
  assert.equal(events(blank)[0].personName, '人物 #7');
});

test('audited r2 profile uses actual expression 5584 and the verified unchanged consumption actions', async () => {
  const f = fixture({profile: 'audited-2026-10-25-r2'}); await f.runtime._LoadDataJson();
  for (const id of workIds) f.actions.get(id).Run();
  f.actions.get(302775837391885).Run(); f.actions.get(423236544410898).Run();
  assert.equal(events(f).length, 10);
  const damage = events(f).at(-1);
  assert.equal(damage.formula.expression, 5584);
  assert.deepEqual(Array.from(damage.formula.inputs), f.inputs);
  assert.equal(damage.amount, 1000000); assert.equal(damage.effectiveAmount, 10000);
  assert.equal(f.messages.some(message => message.type === 'error'), false);
});

test('unknown observer profile installs no engine hooks', () => {
  const scope = {console, setInterval() { throw new Error('must not start'); }};
  const messages = [];
  installTowerCapture(scope, m => messages.push(m), 'unknown');
  assert.equal(messages[0].type, 'error');
  assert.equal(Object.hasOwn(scope, 'C3_CreateRuntime'), false);
});

test('all audited work branches capture exact inputs and output once without re-running game expressions', async () => {
  for (const profile of ['audited-2026-10-25', 'audited-2026-10-25-r2']) {
    const f = fixture({profile}); await f.runtime._LoadDataJson();
    for (let branch = 0; branch < 4; branch++) {
      f.setWorkBranch(branch);
      const receiver = {branch};
      const assignment = branch % 2 ? 308069267939259 : 555212428242208;
      assert.equal(f.actions.get(assignment).Run.call(receiver, 'assignment'), `return:${assignment}`);
      const originalEvaluation = f.calls.filter(call => call[0] === 'work-expression').at(-1);
      assert.equal(originalEvaluation[1], receiver);
      assert.deepEqual(originalEvaluation[2], ['work-evaluation']);
      const amount = f.workParameters[2];
      f.actions.get(branch === 0 ? 217737310598270 : workIds[0]).Run();
      const row = events(f).at(-1);
      assert.equal(row.workFormula.expression, f.workExpressions[branch]);
      assert.deepEqual(row.workFormula.inputs, f.workInputs.slice(0, branch === 0 ? 11 : 10));
      assert.equal(row.workFormula.flatBonus, f.flatBonuses[branch === 0 ? 67 : 64 + branch]);
      assert.equal(row.workFormula.result, amount);
      assert.equal(row.amount, amount);
    }
    assert.equal(events(f).length, 4);
    assert.equal(f.calls.filter(call => call[0] === 'work-expression').length, 4);
    assert.equal(events(f)[0].category, '城墙修复');
    assert.equal(f.messages.some(message => message.type === 'error'), false);
  }
});

test('work formulas never come from previews or another person, target or consumed action', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  f.evaluateWork();
  f.actions.get(workIds[0]).Run();
  assert.equal(events(f).at(-1).workFormula, undefined);
  for (const parameterIndex of [0, 1, 2]) {
    f.actions.get(308069267939259).Run();
    const original = f.workParameters[parameterIndex];
    f.workParameters[parameterIndex]++;
    f.actions.get(workIds[0]).Run();
    assert.equal(events(f).at(-1).workFormula, undefined);
    f.workParameters[parameterIndex] = original;
  }
  f.actions.get(308069267939259).Run(); f.actions.get(workIds[0]).Run();
  assert.ok(events(f).at(-1).workFormula);
  f.actions.get(workIds[0]).Run();
  assert.equal(events(f).at(-1).workFormula, undefined);
});

test('work assignment and evaluation exceptions retain the original failure and produce no success event', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  const original = new Error('work-original-failure'); f.setWorkThrows(original);
  assert.throws(() => f.actions.get(308069267939259).Run(), error => error === original);
  assert.equal(events(f).length, 0);
  f.setWorkThrows(null); f.workInputs[2] = Number.NaN;
  assert.equal(f.actions.get(555212428242208).Run(), 'return:555212428242208');
  assert.ok(f.messages.some(message => message.type === 'error'));
  f.heartbeat(); assert.equal(f.messages.at(-1).type, 'error');
});

test('work categories use actual execution state and person data is a bounded event-time snapshot', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  for (const state of ['伐木', '采矿', '种植', '锻造', '工艺', '炼丹', '建造', '研究', '操练']) {
    f.setExecution(state); f.actions.get(workIds[0]).Run();
    assert.equal(events(f).at(-1).category, state);
  }
  const first = events(f)[0];
  assert.equal(first.person.job, '伐木'); assert.equal(first.person.profession, '测试职业');
  assert.equal(first.person.iconKey, 'person:B6');
  assert.equal(first.person.attributes.find(row => row.label === '力量').value, 10);
  assert.equal(first.person.equipment[0].name, '测试武器');
  assert.equal(first.person.equipment[0].qualityName, '绝世');
  f.metadataRows.set('save:11:7:9', 999);
  f.actions.get(workIds[0]).Run();
  assert.equal(events(f).at(-1).person.equipment, undefined);
  assert.equal(first.person.equipment[0].name, '测试武器');
  f.setMetadataAvailable(false); f.actions.get(workIds[0]).Run();
  assert.deepEqual(events(f).at(-1).person, {});
  assert.equal(events(f).at(-1).category, '采集');
  assert.equal(f.messages.some(message => message.type === 'error'), false);
});

test('attack kind and damage type come from the actual skill table and do not invent cast counts', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  for (const [kind, type, expectedKind, expectedType] of [
    ['普攻', 1, 'basic', 'physical'], ['技能', 3, 'skill', 'magic'], ['普攻', 2, 'basic', 'piercing'],
    ['技能', 4, 'skill', 'unknown'], ['被动', 6, 'unknown', 'unknown'],
  ]) {
    f.setSkill(kind, type); f.setHp(10000);
    f.actions.get(302775837391885).Run(); f.actions.get(423236544410898).Run();
    const row = events(f).at(-1);
    assert.equal(row.attackKind, expectedKind); assert.equal(row.damageType, expectedType);
    assert.equal(row.skillId, '5'); assert.equal(row.skillName, '测试攻击');
    assert.equal(row.actionId, undefined);
    assert.equal(row.person.professionId, '4');
  }
  f.setHp(20); f.damageParameters[4] = 10;
  f.actions.get(994019443997195).Run();
  assert.equal(events(f).at(-1).attackKind, 'periodic');
  assert.equal(events(f).at(-1).formula, undefined);
});

test('invalid optional icon identifiers are omitted rather than rewritten into different assets', async () => {
  const f = fixture(); await f.runtime._LoadDataJson();
  for (const invalid of ['B6\u0000', '../B6', 'B'.repeat(81), ' B6 ', 'person:B6']) {
    f.metadataRows.set('save:7:5:2', invalid);
    f.metadataRows.set('wp:20:2:0', invalid);
    f.actions.get(workIds[0]).Run();
    const person = events(f).at(-1).person;
    assert.equal(person.appearance, undefined);
    assert.equal(person.iconKey, undefined);
    assert.equal(person.equipment[0].iconKey, undefined);
  }
  assert.equal(f.messages.some(message => message.type === 'error'), false);
});
