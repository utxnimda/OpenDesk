<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { Grip, Pin, SlidersHorizontal, X } from '@lucide/vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import { createTowerClient } from './client';
import SpriteIcon from './SpriteIcon.vue';
import { buildRankingBoards, parseRankingBoardFilter, rankingProfessions, type RankingBoard, type RankingBoardFilter, type RankingBoardRow } from './statisticsRanking';
import { formatStatisticsNumber, formatStatisticsOutputNumber, type StatisticsNumberMode } from './statisticsNumber';
import type { AttackKind, DamageType, OutputKind, PersonStatistics } from './statisticsTypes';
import type { VisualCatalog } from './types';
import { useRuntimeStatistics } from './useRuntimeStatistics';

const props = defineProps<{ host: LimitedHost; gameDir: string; initialKind?: OutputKind; initialNumberMode?: StatisticsNumberMode; initialRankingFilter?: unknown }>();
const { response, failure, updatedAt } = useRuntimeStatistics(props.host, () => props.gameDir);
const defaultFilter = (): RankingBoardFilter => ({ kind: props.initialKind ?? 'damage', query: '', workCategories: null,
  damageTypes: null, professions: null, damageGrouping: 'type', sort: 'amount', limit: 10, hideZero: true });
const contextError = ref('');
let initial = defaultFilter();
try {
  const context = props.host.surface?.context?.data ?? { gameDir: props.gameDir, kind: props.initialKind,
    numberMode: props.initialNumberMode, rankingFilter: props.initialRankingFilter };
  if (new TextEncoder().encode(JSON.stringify(context)).byteLength > 8192) throw new Error('窗口筛选上下文超过 8 KiB，请减少筛选项后重新打开。');
  const supplied = props.initialRankingFilter !== undefined ? props.initialRankingFilter : context.rankingFilter;
  if (supplied !== undefined) {
    const parsed = parseRankingBoardFilter(supplied);
    if (!parsed) throw new Error('排行筛选无效或超出限制，请返回主面板重新打开，或明确重置筛选。');
    initial = parsed;
  }
} catch (error) { contextError.value = errorMessage(error); }
const kind = ref<OutputKind>(initial.kind);
const numberMode = ref<StatisticsNumberMode>(props.initialNumberMode ?? 'compact');
const workCategories = ref<string[] | null>(initial.workCategories);
const damageTypes = ref<DamageType[] | null>(initial.damageTypes);
const selectedProfessions = ref<string[] | null>(initial.professions);
const damageGrouping = ref<RankingBoardFilter['damageGrouping']>(initial.damageGrouping);
const damageDimension = ref<'type' | 'attack'>(initial.attackKind ? 'attack' : 'type');
const attackKind = ref<AttackKind>(initial.attackKind ?? 'basic');
const personId = ref(initial.personId);
const query = ref(initial.query);
const sort = ref<RankingBoardFilter['sort']>(initial.kind === 'work' && initial.sort === 'dps' ? 'amount' : initial.sort);
const limit = ref<number | null>(initial.limit);
const hideZero = ref(initial.hideZero);
const opacity = ref(76);
const alwaysOnTop = ref(true);
const windowPending = ref(false);
const windowError = ref('');
const sessionNotice = ref('');
const visuals = shallowRef<VisualCatalog | null>(null);
const visualError = ref('');
const selected = shallowRef<{ person: PersonStatistics; sessionId: string; gameDir: string; visuals: VisualCatalog | null } | null>(null);
const snapshot = computed(() => response.value?.statistics);
const filter = computed<RankingBoardFilter>(() => ({ kind: kind.value, query: query.value, ...(personId.value ? { personId: personId.value } : {}),
  workCategories: workCategories.value, damageTypes: damageTypes.value, professions: selectedProfessions.value,
  damageGrouping: damageGrouping.value, sort: sort.value, limit: limit.value, hideZero: hideZero.value,
  attackKind: damageDimension.value === 'attack' ? attackKind.value : null }));
const boards = computed(() => contextError.value ? [] : buildRankingBoards(snapshot.value, filter.value));
const workChoices = computed(() => [...new Set([...(snapshot.value?.workCategories ?? []),
  ...(snapshot.value?.people.flatMap(person => person.work.map(cell => cell.category)) ?? []), ...(workCategories.value ?? [])])]);
const professions = computed(() => {
  const choices = rankingProfessions(snapshot.value?.people ?? []);
  for (const key of selectedProfessions.value ?? []) if (!choices.some(item => item.value === key)) {
    choices.push({ value: key, label: key === 'unknown' ? '职业未知' : key.slice(6) });
  }
  return choices;
});
const typeChoices: { value: DamageType; label: string }[] = [
  { value: 'physical', label: '力 · 物理' }, { value: 'magic', label: '智 · 魔法' },
  { value: 'piercing', label: '敏 · 穿刺' }, { value: 'unknown', label: '其他 / 未知' },
];
const attackChoices: { value: AttackKind; label: string }[] = [
  { value: 'basic', label: '普攻命中' }, { value: 'skill', label: '技能命中' },
  { value: 'periodic', label: '持续伤害' }, { value: 'unknown', label: '未分类攻击' },
];
const scopeLabel = computed(() => kind.value === 'work' ? '每种工作独立排行' : damageDimension.value === 'attack'
  ? attackChoices.find(choice => choice.value === attackKind.value)?.label : ({ combined: '合并伤害排行', type: '按伤害类型分榜', profession: '按职业分榜' }[damageGrouping.value]));
const countLabel = computed(() => kind.value === 'work' ? '工作次数' : '命中次数');
const sortLabel = computed(() => ({ amount: kind.value === 'work' ? '累计工作量' : '完整伤害', dps: 'DPS', count: countLabel.value, average: '平均单次', latest: '最近单次' }[sort.value]));
const sourceLabel = computed(() => {
  if (!props.host.available) return '需要桌面宿主';
  if (!props.gameDir.trim()) return '未指定游戏目录';
  if (failure.value) return '刷新失败 · 保留上次结果';
  if (!snapshot.value) return '正在连接统计';
  if (snapshot.value.state === 'running' && response.value?.capture.state !== 'connected') return '采集断开 · 等待确认';
  return { idle: '尚未开始计时', running: '正在计时', stopped: '计时已停止', interrupted: '统计已中断' }[snapshot.value.state];
});
const sessionTime = computed(() => {
  const seconds = Math.max(0, Math.floor(snapshot.value?.elapsedRealSeconds ?? 0));
  return `${Math.floor(seconds / 3600).toString().padStart(2, '0')}:${Math.floor(seconds / 60 % 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
});
function number(value: number | null | undefined) { return formatStatisticsOutputNumber(value, numberMode.value); }
function preciseNumber(value: number | null | undefined) { return formatStatisticsNumber(value, numberMode.value); }
function toggleChoice<T extends string>(current: T[] | null, value: T, choices: T[]): T[] {
  const values = current === null ? choices : current;
  return values.includes(value) ? values.filter(item => item !== value) : [...values, value];
}
function toggleWork(value: string) { workCategories.value = toggleChoice(workCategories.value, value, workChoices.value); }
function toggleType(value: DamageType) { damageTypes.value = toggleChoice(damageTypes.value, value, typeChoices.map(choice => choice.value)); }
function toggleProfession(value: string) { selectedProfessions.value = toggleChoice(selectedProfessions.value, value, professions.value.map(choice => choice.value)); }
function resetFilters() {
  const defaults = defaultFilter();
  kind.value = defaults.kind; query.value = ''; personId.value = undefined; workCategories.value = null; damageTypes.value = null;
  selectedProfessions.value = null; damageGrouping.value = 'type'; damageDimension.value = 'type'; attackKind.value = 'basic';
  sort.value = 'amount'; limit.value = 10; hideZero.value = true; contextError.value = '';
}
function barWidth(amount: number, maximum: number): string { return `${maximum > 0 ? Math.max(0, Math.min(100, amount / maximum * 100)) : 0}%`; }
function sortMetric(row: RankingBoardRow): string { return number(sort.value === 'count' ? row.count : sort.value === 'average' ? row.average : row.latestAmount); }
function boardColor(board: RankingBoard): string {
  const colors: Record<string, string> = { physical: '#e6977c', magic: '#b39bef', piercing: '#e6c779', unknown: '#a3b4c9',
    '建造': '#d3ac74', '采矿': '#84b6df', '伐木': '#82c49e', '种植': '#b3c978', '研究': '#b39bef', '炼丹': '#d6a0bc', '锻造': '#d99074', '工艺': '#98c8c1' };
  return colors[board.damageType ?? board.workCategory ?? ''] ?? '#72d6c6';
}
function selectPerson(row: PersonStatistics) {
  selected.value = { person: JSON.parse(JSON.stringify(row)) as PersonStatistics, sessionId: snapshot.value?.sessionId ?? '',
    gameDir: props.gameDir, visuals: visuals.value };
}
async function windowAction(action: 'close' | 'pin' | 'drag') {
  if (windowPending.value) return;
  windowError.value = '';
  if (!props.host.surface) { windowError.value = '需升级宿主以支持透明窗口'; return; }
  windowPending.value = true;
  try {
    if (action === 'close') await props.host.surface.close();
    else if (action === 'drag') await props.host.surface.startDrag();
    else { await props.host.surface.setAlwaysOnTop(!alwaysOnTop.value); alwaysOnTop.value = !alwaysOnTop.value; }
  } catch (error) { windowError.value = errorMessage(error); }
  finally { windowPending.value = false; }
}
watch(kind, () => { if (kind.value === 'work' && sort.value === 'dps') sort.value = 'amount'; });
watch(damageDimension, () => { if (damageDimension.value === 'attack' && damageGrouping.value === 'type') damageGrouping.value = 'combined'; }, { immediate: true });
watch(() => snapshot.value?.sessionId, (current, previous) => {
  if (current && previous && current !== previous) sessionNotice.value = '计时会话已切换，排行已更新；已打开的人物详情仍保留原快照。';
});

const client = createTowerClient(props.host);
const requestedIcons = new Set<string>();
let iconGeneration = 0;
let iconBusy = false;
let disposed = false;
async function loadIcons() {
  if (disposed || iconBusy || !props.host.available || !props.gameDir.trim()) return;
  const currentSelection = selected.value?.gameDir === props.gameDir ? selected.value : null;
  const keys = [...new Set([...boards.value.flatMap(board => board.rows.map(row => row.person.person?.iconKey)),
    currentSelection?.person.person?.iconKey, ...(currentSelection?.person.person?.equipment?.map(item => item.iconKey) ?? [])]
    .filter((key): key is string => Boolean(key)))].filter(key => !requestedIcons.has(key));
  if (!keys.length) return;
  const generation = iconGeneration;
  iconBusy = true;
  keys.forEach(key => requestedIcons.add(key));
  try {
    const result = await client.visuals(props.gameDir, keys);
    if (generation !== iconGeneration || disposed) return;
    visuals.value = { schemaVersion: 1, sheets: { ...visuals.value?.sheets, ...result.sheets }, sprites: { ...visuals.value?.sprites, ...result.sprites } };
    visualError.value = (result.warnings ?? []).join('；');
    if (selected.value?.gameDir === props.gameDir) selected.value = { ...selected.value, visuals: visuals.value };
  } catch (error) { if (generation === iconGeneration && !disposed) visualError.value = `图标未加载，已保留人物名称：${errorMessage(error)}`; }
  finally {
    if (generation === iconGeneration) { iconBusy = false; void loadIcons(); }
  }
}
function retryIcons() { requestedIcons.clear(); visualError.value = ''; void loadIcons(); }
watch([boards, selected], () => { void loadIcons(); });
watch(() => props.gameDir, () => { iconGeneration++; iconBusy = false; requestedIcons.clear(); visuals.value = null; visualError.value = ''; sessionNotice.value = ''; });
onBeforeUnmount(() => { disposed = true; iconGeneration++; });
</script>

<template>
  <section class="statistics-overlay" :style="{ '--overlay-opacity': opacity / 100 }" aria-label="透明统计排行榜">
    <header class="overlay-header">
      <button class="drag-handle" aria-label="拖动排行榜窗口" title="拖动窗口" @pointerdown.prevent="windowAction('drag')"><Grip :size="17" /><span>工作与战斗<span class="header-subtitle">实时排行</span></span></button>
      <button class="icon-button" :aria-pressed="alwaysOnTop" :disabled="windowPending" aria-label="切换窗口置顶" :title="alwaysOnTop ? '已置顶' : '未置顶'" @click="windowAction('pin')"><Pin :size="16" /></button>
      <button class="icon-button" :disabled="windowPending" aria-label="关闭排行榜窗口" @click="windowAction('close')"><X :size="18" /></button>
    </header>
    <div class="source-line"><span class="source-state" :class="{ live: !failure && snapshot?.state === 'running' && response?.capture.state === 'connected' }">{{ sourceLabel }}</span><time>{{ sessionTime }}</time></div>
    <p v-if="failure || windowError" class="overlay-warning" role="alert">{{ failure || windowError }}</p>
    <p v-if="contextError" class="overlay-warning" role="alert">{{ contextError }}<button @click="resetFilters">重置排行筛选</button></p>
    <p v-if="!host.surface" class="overlay-warning">需升级宿主以支持透明窗口</p>
    <p v-if="snapshot?.reason" class="overlay-warning">{{ snapshot.reason }}</p>
    <p v-if="sessionNotice" class="session-notice" role="status">{{ sessionNotice }}<button aria-label="关闭会话切换提示" @click="sessionNotice = ''"><X :size="12" /></button></p>
    <div class="mode-row"><div class="mode-tabs"><button :aria-pressed="kind === 'work'" @click="kind = 'work'">工作量</button><button :aria-pressed="kind === 'damage'" @click="kind = 'damage'">战斗伤害</button></div><span>{{ scopeLabel }}</span></div>
    <details class="overlay-filters">
      <summary><SlidersHorizontal :size="14" /><span>筛选与显示</span><small>{{ limit === null ? '全部' : `Top ${limit}` }} · {{ sortLabel }}</small></summary>
      <div class="filter-grid">
        <label class="wide">人物<input v-model="query" maxlength="200" aria-label="筛选人物" placeholder="名称或编号" /></label>
        <p v-if="personId" class="filter-note wide">限定人物编号 {{ personId }}<button @click="personId = undefined">取消人物限定</button></p>
        <fieldset v-if="kind === 'work'" class="choice-group wide" aria-label="工作类型多选"><legend>工作类型 · 每种一个榜</legend><div class="choice-actions"><button @click="workCategories = null">全部工作</button><button @click="workCategories = []">清空工作</button></div><div class="choice-options"><label v-for="name in workChoices" :key="name"><input type="checkbox" :checked="workCategories === null || workCategories.includes(name)" :aria-label="`工作类型：${name}`" @change="toggleWork(name)" />{{ name }}</label><span v-if="!workChoices.length" class="filter-note">尚无工作类型</span></div></fieldset>
        <template v-else>
          <label class="wide">伤害统计维度<select v-model="damageDimension" aria-label="伤害统计维度"><option value="type">伤害属性 · 可多选</option><option value="attack">攻击类别 · 独立口径</option></select></label>
          <fieldset v-if="damageDimension === 'type'" class="choice-group wide" aria-label="伤害类型多选"><legend>伤害类型</legend><div class="choice-actions"><button @click="damageTypes = null">全部伤害类型</button><button @click="damageTypes = []">清空伤害类型</button></div><div class="choice-options"><label v-for="choice in typeChoices" :key="choice.value"><input type="checkbox" :checked="damageTypes === null || damageTypes.includes(choice.value)" :aria-label="`伤害类型：${choice.label}`" @change="toggleType(choice.value)" />{{ choice.label }}</label></div></fieldset>
          <label v-else class="wide">攻击类别<select v-model="attackKind" aria-label="攻击类别"><option v-for="choice in attackChoices" :key="choice.value" :value="choice.value">{{ choice.label }}</option></select></label>
          <p v-if="damageDimension === 'attack'" class="filter-note wide">攻击类别独立统计，不与伤害属性筛选交叉。</p>
          <label class="wide">战斗分榜<select v-model="damageGrouping" aria-label="伤害分榜"><option value="combined">合并成一个榜</option><option v-if="damageDimension === 'type'" value="type">按伤害类型分榜</option><option value="profession">按职业分榜</option></select></label>
        </template>
        <fieldset class="choice-group wide" aria-label="职业多选"><legend>职业</legend><div class="choice-actions"><button @click="selectedProfessions = null">全部职业</button><button @click="selectedProfessions = []">清空职业</button></div><div class="choice-options"><label v-for="item in professions" :key="item.value"><input type="checkbox" :checked="selectedProfessions === null || selectedProfessions.includes(item.value)" :aria-label="`职业：${item.label}`" @change="toggleProfession(item.value)" />{{ item.label }}</label><span v-if="!professions.length" class="filter-note">尚无人物职业</span></div></fieldset>
        <label>排序<select v-model="sort" aria-label="排行排序"><option value="amount">累计量</option><option v-if="kind === 'damage'" value="dps">每秒伤害</option><option value="count">{{ countLabel }}</option><option value="average">平均单次</option><option value="latest">最近单次</option></select></label>
        <label>显示人数<select v-model="limit" aria-label="排行人数"><option :value="5">Top 5</option><option :value="10">Top 10</option><option :value="20">Top 20</option><option :value="null">全部</option></select></label>
        <label class="wide">数字<select v-model="numberMode" aria-label="排行数值显示"><option value="compact">中文单位 · 4位有效数字</option><option value="raw">完整数字</option></select></label>
        <label class="wide opacity-control">背景不透明度 <output>{{ opacity }}%</output><input v-model.number="opacity" type="range" min="30" max="100" step="1" aria-label="背景不透明度" /></label>
        <label class="check-control wide"><input v-model="hideZero" type="checkbox" aria-label="隐藏零输出人物" />隐藏零输出人物</label>
      </div>
    </details>
    <div class="ranking-summary"><span>{{ boards.length }} 个榜 · 每榜 {{ limit === null ? '全部' : `Top ${limit}` }}</span><small><template v-if="sort !== 'amount'">按{{ sortLabel }}排序；</template>条形相对本榜最大累计量 · 占比按本榜全部筛选人物</small></div>
    <div class="ranking-boards" aria-label="人物输出排行">
      <section v-for="board in boards" :key="board.key" class="ranking-board" :data-board-key="board.key" :style="{ '--board-accent': boardColor(board) }" :aria-label="`${board.label}排行榜`">
        <header class="board-header"><h2>{{ board.label }}</h2><span>{{ board.peopleCount }} 人 · 累计 {{ number(board.amount) }}</span></header>
        <ol class="ranking-list">
          <li v-for="row in board.rows" :key="row.person.personId" :data-person-id="row.person.personId">
            <button class="ranking-person" :aria-label="`查看${row.person.personName}人物快照`" @click="selectPerson(row.person)">
              <span class="row-bar" aria-hidden="true" :style="{ width: barWidth(row.amount, board.maximumAmount) }" />
              <span class="rank" :class="{ podium: row.rank <= 3 }">{{ row.rank }}</span>
              <SpriteIcon :icon-key="row.person.person?.iconKey" :visuals="visuals" :label="row.person.personName" :size="32" fallback="person" />
              <span class="row-body"><span class="row-head"><strong>{{ row.person.personName }}</strong><span class="row-value">{{ number(row.amount) }}<small>{{ (row.share * 100).toFixed(1) }}%</small></span></span><span v-if="kind === 'work'" class="row-foot"><span>最近实际 {{ number(row.latestAmount) }}</span><span>{{ number(row.count) }} 次</span></span><span v-else class="row-foot"><span>{{ row.person.person?.profession || '职业未知' }}</span><span>{{ number(row.dps) }} / 秒</span></span><span v-if="sort === 'count' || sort === 'average' || sort === 'latest'" class="sort-metric" aria-label="排序依据">{{ sortLabel }} · {{ sortMetric(row) }}<template v-if="sort === 'count'"> 次</template></span></span>
            </button>
          </li>
          <li v-if="!board.rows.length" class="ranking-empty">此榜暂无符合筛选的输出</li>
        </ol>
      </section>
      <p v-if="!boards.length" class="ranking-empty">{{ contextError ? '筛选无效，已暂停展示排行' : snapshot?.state === 'idle' ? '请在主面板开始计时。此窗口会自动同步。' : '暂无符合筛选的榜单；可调整类型、职业或零输出筛选' }}</p>
    </div>
    <footer class="overlay-footer"><span>{{ kind === 'damage' ? '次数为命中，不是技能施放次数' : '实际工作量包含超出任务剩余需求的部分' }}</span><small>更新 {{ updatedAt || '—' }} · 计时控制在主面板</small><p v-if="visualError" role="status">{{ visualError }}<button @click="retryIcons">重试图标</button></p></footer>
    <section v-if="selected" class="person-detail" role="dialog" aria-label="排行榜人物快照" @keydown.esc.stop="selected = null">
      <header><SpriteIcon :icon-key="selected.person.person?.iconKey" :visuals="selected.visuals" :label="selected.person.personName" :size="36" fallback="person" /><div><small>已固定人物快照</small><h3>{{ selected.person.personName }}</h3></div><button class="icon-button" aria-label="关闭人物快照" @click="selected = null"><X :size="18" /></button></header>
      <div class="person-detail-body"><p class="detail-note">{{ selected.person.personSequence == null ? '尚未采集人物资料' : `资料来自事件 #${selected.person.personSequence}` }}<span v-if="selected.sessionId !== snapshot?.sessionId"> · 上一计时会话</span><span v-if="selected.gameDir !== gameDir"> · 原游戏目录</span></p><dl><dt>编号</dt><dd>{{ selected.person.personId }}</dd><dt>职业</dt><dd>{{ selected.person.person?.profession || '未知 / 未采集' }}</dd><dt>工作</dt><dd>{{ selected.person.person?.job || '未知 / 未采集' }}</dd><dt>外观</dt><dd>{{ selected.person.person?.appearance || '未知 / 未采集' }}</dd></dl><p v-if="selected.person.person?.professionDescription" class="detail-note">{{ selected.person.person.professionDescription }}</p>
        <div v-if="selected.person.person?.attributes?.length" class="attribute-grid"><div v-for="attribute in selected.person.person.attributes" :key="attribute.id"><span>{{ attribute.label }}</span><strong>{{ preciseNumber(attribute.value) }}</strong></div></div><p v-else class="detail-note">属性未采集</p>
        <h4>装备</h4><ul v-if="selected.person.person?.equipment?.length" class="equipment-list"><li v-for="(item, index) in selected.person.person.equipment" :key="index"><SpriteIcon :icon-key="item.iconKey" :visuals="selected.visuals" :label="item.name" :size="26" fallback="item" /><span>{{ item.slot }} {{ item.name }}<small>{{ item.type }} {{ item.level == null ? '' : `${item.level} 级` }} {{ item.qualityName }}</small></span></li></ul><p v-else class="detail-note">装备未采集</p><p class="detail-note">仅展示事件中取得的资料；刷新和新会话不会替换这份快照。</p>
      </div>
    </section>
  </section>
</template>

<style>
:root[data-surface="statistics-overlay"],
:root[data-surface="statistics-overlay"] body,
:root[data-surface="statistics-overlay"] #app {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
  background: transparent;
}
</style>

<style scoped>
.statistics-overlay { --overlay-accent: #72e2d6; --overlay-muted: #acc1ce; --accent: var(--overlay-accent); box-sizing: border-box; position: relative; display: flex; flex-direction: column; height: 100vh; min-height: 250px; overflow: hidden; color: #edf6fb; background: rgba(16, 25, 37, var(--overlay-opacity)); border: 1px solid #c3dce22b; border-radius: 15px; font: 12px/1.5 "Segoe UI", "Microsoft YaHei", sans-serif; font-variant-numeric: tabular-nums; }
button, input, select { font: inherit; }
button { color: inherit; cursor: pointer; }
button:disabled { cursor: wait; opacity: .5; }
button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid var(--overlay-accent); outline-offset: 2px; }
.overlay-header { display: flex; align-items: center; gap: 4px; padding: 10px 10px 4px; }
.drag-handle { flex: 1; display: flex; align-items: center; gap: 8px; text-align: left; border: 0; background: transparent; padding: 5px 3px; font-size: 14px; font-weight: 600; cursor: grab; touch-action: none; }
.drag-handle > svg { color: var(--overlay-muted); }
.header-subtitle { margin-left: 8px; color: var(--overlay-muted); font-size: 10px; font-weight: 400; letter-spacing: 1px; }
.icon-button { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: var(--overlay-muted); }
.icon-button:hover, .icon-button[aria-pressed="true"] { color: var(--overlay-accent); background: #72e2d618; }
.source-line { display: flex; align-items: center; justify-content: space-between; padding: 0 15px 9px; color: var(--overlay-muted); font-size: 11px; }
.source-state::before { display: inline-block; content: ''; width: 5px; height: 5px; margin: 0 6px 1px 0; border-radius: 50%; background: #91a3ae; }
.source-state.live::before { background: #76e3b1; box-shadow: 0 0 7px #76e3b15c; }
.overlay-warning, .session-notice { margin: 3px 12px 7px; padding: 7px 9px; border-radius: 6px; background: #f6ca6420; color: #ffe3a7; font-size: 11px; overflow-wrap: anywhere; }
.overlay-warning button, .filter-note button { margin-left: 8px; border: 0; padding: 0; background: transparent; color: var(--overlay-accent); text-decoration: underline; }
.session-notice { background: #72e2d618; color: #c4eee8; }
.session-notice button { border: 0; background: transparent; vertical-align: middle; }
.mode-row { display: flex; align-items: center; gap: 10px; justify-content: space-between; padding: 0 12px 9px; }
.mode-row > span { color: var(--overlay-muted); font-size: 11px; }
.mode-tabs { display: flex; padding: 2px; border-radius: 7px; background: #0003; }
.mode-tabs button { padding: 4px 12px; border: 0; border-radius: 5px; background: transparent; color: var(--overlay-muted); }
.mode-tabs button[aria-pressed="true"] { color: #dcfff8; background: #72e2d627; }
.overlay-filters { border-top: 1px solid #a4c4d318; border-bottom: 1px solid #a4c4d318; flex-shrink: 0; }
.overlay-filters summary { display: flex; align-items: center; gap: 7px; padding: 8px 14px; cursor: pointer; color: var(--overlay-muted); list-style: none; }
.overlay-filters summary::-webkit-details-marker { display: none; }
.overlay-filters summary small { margin-left: auto; font-size: 10px; }
.filter-grid { padding: 3px 14px 11px; display: grid; grid-template-columns: 1fr 1fr; gap: 9px 10px; max-height: 38vh; overflow-y: auto; }
.filter-grid label { display: flex; flex-direction: column; gap: 3px; color: var(--overlay-muted); font-size: 11px; min-width: 0; }
.filter-grid input:not([type]), .filter-grid select { box-sizing: border-box; width: 100%; min-width: 0; padding: 5px 6px; color: #eaf3f8; border: 1px solid #b4cfde24; border-radius: 5px; background: #0c1424b0; }
.filter-grid select option { background: #14202e; color: #eaf3f8; }
.filter-grid .wide { grid-column: 1 / -1; }
.choice-group { min-width: 0; padding: 7px 8px 9px; margin: 0; border: 1px solid #b4cfde24; border-radius: 6px; }
.choice-group legend { color: #d8e7ed; padding: 0 4px; font-size: 11px; }
.choice-actions { display: flex; gap: 12px; margin-bottom: 7px; }
.choice-actions button { padding: 0; border: 0; background: transparent; color: var(--overlay-accent); font-size: 11px; }
.choice-options { display: flex; flex-wrap: wrap; gap: 6px 12px; max-height: 130px; overflow: auto; }
.filter-grid .choice-options label { flex-direction: row; align-items: center; gap: 4px; color: #d3e1e9; }
.choice-options input { margin: 0; accent-color: var(--overlay-accent); }
.filter-note { margin: 0; color: var(--overlay-muted); font-size: 11px; overflow-wrap: anywhere; }
.filter-grid .opacity-control { flex-direction: row; flex-wrap: wrap; align-items: center; }
.opacity-control output { margin-left: auto; }
.opacity-control input { width: 100%; accent-color: #72e2d6; }
.filter-grid .check-control { flex-direction: row; align-items: center; gap: 5px; }
.check-control input { accent-color: #72e2d6; }
.ranking-summary { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 3px 10px; padding: 9px 14px; color: var(--overlay-muted); font-size: 11px; }
.ranking-summary small { font-size: 10px; color: #9bb3c0; }
/* Implicit rows must retain their content height inside the bounded scroller.
   Auto rows can shrink when clipped grid items have a zero automatic minimum. */
.ranking-boards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)); grid-auto-rows: max-content; gap: 10px; align-content: start; align-items: start; min-height: 0; flex: 1; overflow-y: auto; padding: 0 10px 10px; scrollbar-width: thin; scrollbar-color: #95b6c440 transparent; }
.ranking-board { min-width: 0; min-height: min-content; border: 1px solid #a4c4d31b; border-radius: 8px; overflow: hidden; background: #07111a20; }
.board-header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 3px 8px; padding: 9px 10px; border-bottom: 1px solid #a4c4d31b; }
.board-header h2 { margin: 0; font-size: 13px; color: var(--board-accent); font-weight: 600; }
.board-header > span { color: #b9ced9; font-size: 11px; overflow-wrap: anywhere; }
.ranking-list { list-style: none; padding: 5px; margin: 0; }
.ranking-list > li { margin-bottom: 4px; }
.ranking-list > li:last-child { margin-bottom: 0; }
.ranking-person { width: 100%; position: relative; overflow: hidden; isolation: isolate; display: flex; align-items: center; gap: 7px; padding: 8px 6px; text-align: left; border: 1px solid #a4c4d310; border-radius: 5px; background: #0a142b40; }
.ranking-person:hover { border-color: var(--board-accent); }
.row-bar { position: absolute; inset: 0 auto 0 0; pointer-events: none; background: linear-gradient(90deg, color-mix(in srgb, var(--board-accent) 35%, transparent), color-mix(in srgb, var(--board-accent) 15%, transparent)); }
.ranking-person > :not(.row-bar) { position: relative; z-index: 1; }
.rank { width: 15px; flex-shrink: 0; text-align: center; color: #8ba3b1; font-size: 11px; }
.rank.podium { color: var(--board-accent); font-weight: 700; }
.row-body { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 3px; }
.row-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.row-head > strong { min-width: 0; flex: 1; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font-size: 13px; font-weight: 600; }
.row-value { max-width: 65%; text-align: right; font-size: 15px; color: #f2fffc; overflow-wrap: anywhere; font-weight: 600; }
.row-value small { display: inline-block; margin-left: 7px; font-size: 11px; font-weight: 400; color: #c2d5df; }
.row-foot { display: flex; justify-content: space-between; gap: 7px; color: #bfd2dc; font-size: 11px; overflow-wrap: anywhere; }
.sort-metric { align-self: flex-end; max-width: 100%; padding: 1px 5px; border: 1px solid #b4cfde24; border-radius: 4px; background: #06131d45; color: #dceef4; font-size: 10px; overflow-wrap: anywhere; }
.ranking-empty { padding: 35px 15px; text-align: center; color: var(--overlay-muted); }
.ranking-boards > .ranking-empty { grid-column: 1 / -1; }
.overlay-footer { display: flex; flex-direction: column; gap: 2px; padding: 8px 14px 10px; border-top: 1px solid #accbd71a; color: #9bb3c0; font-size: 10px; }
.overlay-footer small { color: #809aa9; font-size: 9px; }
.overlay-footer p { margin: 3px 0; color: #e2c792; overflow-wrap: anywhere; }
.overlay-footer button { border: 0; background: transparent; color: var(--overlay-accent); text-decoration: underline; }
.person-detail { position: absolute; z-index: 3; inset: 78px 10px 10px; display: flex; flex-direction: column; background: #13202ff5; border: 1px solid #8fdccc5c; border-radius: 12px; box-shadow: 0 10px 35px #0005; overflow: hidden; }
.person-detail > header { display: flex; align-items: center; gap: 10px; padding: 13px; border-bottom: 1px solid #accbd71c; }
.person-detail header > div { flex: 1; }
.person-detail h3 { margin: 0; font-size: 15px; }
.person-detail header small { color: #8eb5c1; font-size: 10px; }
.person-detail-body { padding: 0 14px 14px; overflow-y: auto; }
.detail-note { color: #a7becb; font-size: 11px; line-height: 1.7; }
.person-detail dl { display: grid; grid-template-columns: 40px 1fr; gap: 5px 10px; margin: 12px 0; }
.person-detail dt { color: #9db7c5; }
.person-detail dd { margin: 0; overflow-wrap: anywhere; }
.attribute-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 12px 0; }
.attribute-grid > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 7px; border-radius: 5px; background: #c5e3f009; }
.attribute-grid span { color: #a6bfcc; font-size: 11px; }
.attribute-grid strong { font-weight: 500; }
.person-detail h4 { font-size: 12px; margin: 15px 0 7px; }
.equipment-list { list-style: none; padding: 0; margin: 0; }
.equipment-list li { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
.equipment-list small { display: block; color: #9eb6c3; font-size: 10px; }
@media (max-width: 350px) { .header-subtitle { display: none; } .ranking-person { gap: 5px; padding-inline: 4px; } .row-value { font-size: 13px; } .row-value small { margin-left: 4px; } }
</style>
