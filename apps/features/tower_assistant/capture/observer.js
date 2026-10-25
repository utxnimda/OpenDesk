/* OpenDesk's original observer for explicitly audited game profiles.
 * Installed before the engine in its Runtime worker. All engine calls retain
 * their receiver, arguments, return value and exceptions. No game values are set.
 */
function installTowerCapture(scope, send, ruleProfile) {
  'use strict';
  if (ruleProfile === undefined) ruleProfile = 'audited-2026-10-25';
  // Both audited profiles share the fourteen action SIDs, parameters, table
  // fields and ancestor conditions. Compiled expression indexes differ.
  const sharedActions = {workActions: [
    [382847177923468, '采集'], [890260672342582, '采集'], [481758359077511, '采集'],
    [976952201246496, '研究'], [262198584001088, '操练'], [852732572548979, '锻造与工艺'],
    [696683569195655, '炼丹'], [218834594110811, '建造'], [217737310598270, '城墙修复'],
  ], workAssignments: [308069267939259, 555212428242208],
    damageAssign: 302775837391885, damageApply: 423236544410898, buffApply: 994019443997195};
  const profiles = {
    'audited-2026-10-25': {...sharedActions, damageExpression: 5579, workExpressions: [5615, 5616, 5617, 5618]},
    'audited-2026-10-25-r2': {...sharedActions, damageExpression: 5584, workExpressions: [5620, 5621, 5622, 5623]},
  };
  if (!Object.hasOwn(profiles, ruleProfile)) {
    try { send({type: 'error', error: '未支持的采集规则版本'}); }
    catch (_) { scope.console.error('OpenDesk capture rejected unsupported profile'); }
    return;
  }
  const profile = profiles[ruleProfile];
  const workActions = new Map(profile.workActions);
  const {damageAssign, damageApply, buffApply, damageExpression, workAssignments, workExpressions} = profile;
  let runtime = null;
  let sequence = 0;
  let failed = false;
  let ready = false;
  let activeDamage = null;
  let pendingDamage = null;
  let activeWork = null;
  let pendingWork = null;
  let calculationHooked = false;
  const report = message => {
    try { send(message); }
    catch (error) {
      failed = true;
      // A failed transport must not change the running game. Heartbeats retry
      // the error, and their absence independently makes the reader disconnect.
      scope.console.error('OpenDesk capture transport failed', String(error));
    }
  };
  const fail = code => { failed = true; report({type: 'error', error: code}); };
  const observe = (stage, fn) => {
    try { return fn(); }
    catch (_) { fail(`观察失败：${stage}；当前统计不完整，请停止并检查采集版本`); return null; }
  };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const parameters = name => runtime.GetEventSheetManager().GetFunctionBlockByName(name).CaptureFunctionParameters();
  const text = (value, limit = 100) => {
    if (typeof value !== 'string') return undefined;
    const clean = Array.from(value.replace(/[\x00-\x1f\x7f]/g, '').trim());
    if (!clean.length) return undefined;
    return clean.length > limit ? clean.slice(0, limit - 1).join('') + '…' : clean.join('');
  };
  const iconIdentifier = value => typeof value === 'string' && value.length > 0 && value.length <= 80
    && value === value.trim() && !/[\\/:\x00-\x1f\x7f]/.test(value) ? value : undefined;
  const arrayValue = (name, x, y, z = 0) => {
    try { return runtime.GetObjectClassByName(name)?.GetInstances()[0]?.GetSdkInstance().At(x, y, z); }
    catch (_) { return undefined; } // Optional metadata remains absent when unavailable.
  };
  const currentWork = id => {
    try {
      const core = runtime.GetObjectClassByName('角色核心')?.GetInstances()
        .find(instance => instance.GetInstanceVariableValue(0) === id);
      return text(core?.GetInstanceVariableValue(6));
    } catch (_) { return undefined; }
  };
  const personSnapshot = id => {
    const person = {};
    const professionId = arrayValue('save', id, 6, 2);
    if (Number.isSafeInteger(professionId) && professionId > 0 && arrayValue('zy', professionId, 0) === professionId) {
      person.professionId = String(professionId);
      person.profession = text(arrayValue('zy', professionId, 1));
      person.professionDescription = text(arrayValue('zy', professionId, 5), 100);
    }
    const appearance = iconIdentifier(arrayValue('save', id, 5, 2));
    if (appearance) {
      person.appearance = appearance;
      person.iconKey = `person:${appearance}`;
    }
    person.job = currentWork(id) || text(arrayValue('save', id, 9, 2));
    const attributes = [[1, '力量'], [2, '敏捷'], [3, '智力'], [4, '物理攻击'],
      [5, '穿刺攻击'], [6, '法术攻击'], [7, '神圣攻击']].flatMap(([attribute, label]) => {
      const value = arrayValue('属性临时', attribute, id);
      return finite(value) && Math.abs(value) <= 1e100 ? [{id: String(attribute), label, value}] : [];
    });
    if (attributes.length) person.attributes = attributes;
    const equipment = [];
    const qualityNames = ['未知', '凡物', '优秀', '精致', '绝世', '传说', '神话'];
    for (const [index, slot] of ['武器', '手套', '腰带', '饰品', '内丹', '宝物'].entries()) {
      const equipped = arrayValue('save', id, 16 + index, 2);
      if (!Number.isSafeInteger(equipped) || equipped <= 0 || !arrayValue('save', equipped, 0, 9)
        || arrayValue('save', equipped, 7, 9) !== id) continue;
      const template = arrayValue('save', equipped, 1, 9);
      if (!Number.isSafeInteger(template) || template <= 0 || arrayValue('wp', template, 0) !== template
        || arrayValue('wp', template, 6) !== index + 1) continue;
      const name = text(arrayValue('wp', template, 1), 40);
      if (!name) continue;
      const item = {slot, name};
      const icon = iconIdentifier(arrayValue('wp', template, 2));
      if (icon) item.iconKey = `item:${icon}`;
      const type = text(arrayValue('wp', template, 7), 40);
      if (type) item.type = type;
      const level = arrayValue('save', equipped, 6, 9);
      if (finite(level) && level >= 0 && level <= 1e100) item.level = level;
      const quality = arrayValue('save', equipped, 5, 9);
      if (Number.isInteger(quality) && quality >= 1 && quality <= 6) item.qualityName = qualityNames[quality];
      equipment.push(item);
    }
    if (equipment.length) person.equipment = equipment;
    return Object.fromEntries(Object.entries(person).filter(([, value]) => value !== undefined));
  };
  const attackMetadata = (skillId, periodic) => {
    const skillType = arrayValue('jn', skillId, 3);
    const row = {attackKind: periodic ? 'periodic' : skillType === '普攻' ? 'basic' : skillType === '技能' ? 'skill' : 'unknown',
      damageType: ({1: 'physical', 2: 'piercing', 3: 'magic'})[arrayValue('jn', skillId, 8)] || 'unknown'};
    if (Number.isSafeInteger(skillId) && skillId >= 0) row.skillId = String(skillId);
    const skillName = text(arrayValue('jn', skillId, 1));
    if (skillName) row.skillName = skillName;
    return row;
  };
  const personName = id => {
    // Names are optional metadata. The record explicitly falls back if the
    // authoritative save array has not been created or the name is unavailable.
    try {
      const object = runtime.GetObjectClassByName('save');
      const value = object?.GetInstances()[0]?.GetSdkInstance().At(id, 1, 2);
      if (typeof value === 'string') {
        const clean = Array.from(value.replace(/[\x00-\x1f\x7f]/g, '').trim()).slice(0, 100).join('');
        if (clean) return clean;
      }
    } catch (_) { /* Unknown names are represented by the explicit fallback. */ }
    return `人物 #${id}`;
  };
  const emit = event => {
    if (!finite(event.amount) || event.amount < 0 || !Number.isSafeInteger(event.personId) || event.personId < 0) {
      fail('采集数值或人物编号不符合当前版本规则'); return;
    }
    const row = {schemaVersion: 1, sequence: ++sequence, ...event,
      personName: personName(event.personId), personId: String(event.personId)};
    const gameTime = runtime.GetGameTime();
    if (finite(gameTime) && gameTime >= 0) row.gameTime = gameTime;
    report({type: 'event', event: row});
  };
  function wrapRun(action, before, after) {
    const original = action.Run;
    action.Run = function(...args) {
      const snapshot = observe('动作前状态', before);
      const result = Reflect.apply(original, this, args);
      if (snapshot !== null) observe('动作后状态', () => after(snapshot));
      return result;
    };
  }
  function installActions() {
    if (ready) throw new Error('duplicate-runtime-initialisation');
    const manager = runtime.GetEventSheetManager();
    const ids = [...workActions.keys(), ...workAssignments, damageAssign, damageApply, buffApply];
    const actions = new Map(ids.map(id => [id, manager.GetActionBySID(id)]));
    const damageFunction = manager.GetFunctionBlockByName('战斗伤害计算');
    // Verify the complete fixed mapping before changing any action.
    if ([...actions.values()].some(action => !action || typeof action.Run !== 'function') ||
        !calculationHooked || !damageFunction || damageFunction.GetReturnType() !== 1 || damageFunction.IsAsync()) {
      throw new Error('unsupported-action-map');
    }
    for (const id of workAssignments) {
      const action = actions.get(id), original = action.Run;
      action.Run = function(...args) {
        const previous = activeWork;
        const frame = {formula: null};
        activeWork = frame; pendingWork = null;
        try {
          const result = Reflect.apply(original, this, args);
          observe('工作计算归属', () => {
            const p = parameters('采集核心碰撞');
            pendingWork = {...frame, personId: p[0], targetUID: p[1], amount: p[2]};
          });
          return result;
        } finally { activeWork = previous; }
      };
    }
    for (const [id, fallbackCategory] of workActions) {
      wrapRun(actions.get(id), () => {
        const p = parameters('采集核心碰撞');
        const execution = currentWork(p[0]);
        const category = fallbackCategory === '城墙修复' ? fallbackCategory :
          ['伐木', '采矿', '种植', '锻造', '工艺', '炼丹', '建造', '研究', '操练'].includes(execution) ? execution : fallbackCategory;
        return {p, category, person: personSnapshot(p[0]), calculation: pendingWork};
      }, ({p, category, person, calculation}) => {
        pendingWork = null;
        const row = {kind: 'work', personId: p[0], person, category, amount: p[2]};
        if (calculation && calculation.personId === p[0] && calculation.targetUID === p[1]
          && calculation.amount === p[2] && calculation.formula?.result === p[2]) row.workFormula = calculation.formula;
        emit(row);
      });
    }
    const assignment = actions.get(damageAssign);
    const originalAssignment = assignment.Run;
    assignment.Run = function(...args) {
      const previous = activeDamage;
      const frame = {amount: null, formula: null};
      activeDamage = frame;
      pendingDamage = null;
      try {
        const result = Reflect.apply(originalAssignment, this, args);
        observe('伤害计算归属', () => {
          const p = parameters('战斗碰撞伤害');
          pendingDamage = {...frame, personId: p[0], skillId: p[1], targetUID: p[2], applied: p[4]};
        });
        return result;
      } finally { activeDamage = previous; }
    };
    for (const id of [damageApply, buffApply]) {
      wrapRun(actions.get(id), () => {
        const p = parameters('战斗碰撞伤害');
        const target = runtime.GetInstanceByUID(p[2]);
        if (!target || target.GetObjectClass().GetName() !== '敌人核心') throw new Error('unexpected-target');
        return {p, target, hp: target.GetInstanceVariableValue(2), calculation: pendingDamage,
          person: personSnapshot(p[0]), metadata: attackMetadata(p[1], id === buffApply)};
      }, ({p, target, hp, calculation, person, metadata}) => {
        pendingDamage = null;
        const hpAfter = target.GetInstanceVariableValue(2);
        if (!finite(hp) || !finite(hpAfter)) throw new Error('invalid-hp');
        const isBuff = id === buffApply;
        const matched = !isBuff && calculation && calculation.personId === p[0] &&
          calculation.targetUID === p[2] && calculation.applied === p[4] && finite(calculation.amount);
        if (!isBuff && !matched) throw new Error('missing-calculation');
        const row = {kind: 'damage', personId: p[0], person, ...metadata,
          category: isBuff ? '持续伤害' : metadata.skillName || `攻击 #${p[1]}`,
          amount: isBuff ? p[4] : calculation.amount,
          effectiveAmount: Math.max(0, Math.max(0, hp) - Math.max(0, hpAfter)),
          formulaCoverage: isBuff ? 'stored-buff-value-only' : calculation.formula ? 'main-product' : 'special-return'};
        if (matched && calculation.formula) row.formula = calculation.formula;
        emit(row);
      });
    }
    ready = true;
    report({type: 'ready'});
  }
  function hookAssignment(name, wrap) {
    if (Object.hasOwn(scope, name)) { fail(`采集安装时机过晚：${name}`); return; }
    let current;
    Object.defineProperty(scope, name, {configurable: true, get() { return current; },
      set(original) { current = wrap(original); }});
  }
  hookAssignment('C3_CreateRuntime', original => function(...args) {
    const result = Reflect.apply(original, this, args);
    runtime = result;
    observe('引擎加载入口', () => {
      // Construct binds the PROTOTYPE method directly while building expression
      // nodes. An instance override after loading would never see those calls.
      // Install before _LoadDataJson creates/binds any event expressions.
      const prototype = scope.C3.EventBlock.prototype;
      const calculation = prototype.RunAsExpressionFunctionCall;
      if (typeof calculation !== 'function' || calculationHooked) throw new Error('unsupported-function-binding');
      prototype.RunAsExpressionFunctionCall = function(...callArgs) {
        const returned = Reflect.apply(calculation, this, callArgs);
        if (activeDamage) observe('伤害函数返回', () => {
          if (this.GetScopeParent().GetFunctionName() === '战斗伤害计算') activeDamage.amount = returned;
        });
        return returned;
      };
      calculationHooked = true;
      const load = runtime._LoadDataJson;
      if (typeof load !== 'function') throw new Error('unsupported-load-entry');
      runtime._LoadDataJson = function(...loadArgs) {
        const loaded = Reflect.apply(load, this, loadArgs);
        // Register our continuation before returning the original Promise to
        // Runtime.Init's await. Hooks are installed before Init resumes and can
        // start event processing. Promise identity and rejection are preserved.
        if (loaded && typeof loaded.then === 'function') {
          loaded.then(() => observe('动作映射安装', installActions), () => fail('游戏事件表加载失败'));
        } else observe('动作映射安装', installActions);
        return loaded;
      };
    });
    return result;
  });
  hookAssignment('C3_ExpressionFuncs', table => {
    if (!Array.isArray(table) || [damageExpression, ...workExpressions].some(index => typeof table[index] !== 'function')) {
      fail('工作或伤害公式索引不符合已核验版本'); return table;
    }
    const factory = table[damageExpression];
    table[damageExpression] = function(parameter) {
      const evaluate = Reflect.apply(factory, this, [parameter]);
      return function(...args) {
        const result = Reflect.apply(evaluate, this, args);
        if (activeDamage) observe('主伤害公式', () => {
          const inputs = Array.from({length: 14}, (_, index) => parameter._GetNode(index).GetVar().GetValue());
          if (!inputs.every(finite) || !finite(result)) throw new Error('invalid-formula-input');
          activeDamage.formula = {expression: damageExpression, inputs, result};
        });
        return result;
      };
    };
    for (const [branch, expression] of workExpressions.entries()) {
      const originalFactory = table[expression];
      table[expression] = function(parameter) {
        const evaluate = Reflect.apply(originalFactory, this, [parameter]);
        return function(...args) {
          const result = Reflect.apply(evaluate, this, args);
          if (activeWork) observe('工作公式', () => {
            const count = branch === 0 ? 11 : 10;
            const inputs = Array.from({length: count}, (_, index) => parameter._GetNode(index).GetVar().GetValue());
            const personId = parameter._GetNode(count + 1).GetVar().GetValue();
            // Arr.At is the audited pure array getter. Read the same cached
            // scalar, never re-evaluate an expression or call a game function.
            const flatBonus = arrayValue('属性临时', branch === 0 ? 67 : 64 + branch, personId);
            if (!inputs.every(finite) || !finite(result) || !finite(flatBonus)) throw new Error('invalid-work-formula-input');
            activeWork.formula = {expression, inputs, flatBonus, result};
          });
          return result;
        };
      };
    }
    return table;
  });
  scope.setInterval(() => report(failed ? {type: 'error', error: '采集已发生观察或传输错误，当前统计不完整'} :
    {type: 'heartbeat', ready}), 1000);
}
export {installTowerCapture};
