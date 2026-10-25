<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { Activity, Clock3, PanelsTopLeft, Play, RefreshCw, Square, Wrench } from '@lucide/vue';
import { errorMessage, type LimitedHost } from '../../../framework/module-sdk/src/index';
import type { AttackCell, AttackKind, DamageType, OutputEvent, OutputKind, PersonSnapshot, PersonStatistics, StatisticsOperation, WorkCell } from './statisticsTypes';
import type { VisualCatalog } from './types';
import { createTowerClient } from './client';
import SpriteIcon from './SpriteIcon.vue';
import { formatStatisticsNumber, formatStatisticsOutputNumber, type StatisticsNumberMode } from './statisticsNumber';
import { buildRankingBoards, parseRankingBoardFilter, rankingProfessions, type RankingBoardFilter } from './statisticsRanking';
import { useRuntimeStatistics } from './useRuntimeStatistics';

const props = defineProps<{ host: LimitedHost; gameDir: string }>();
const { response, pending, failure, actionFailure, updatedAt, request, pinned, pinEvent, pinLatest, closePinned, explainPinned } = useRuntimeStatistics(props.host, () => props.gameDir);
const kind = ref<OutputKind>('work');
const numberMode = ref<StatisticsNumberMode>('raw');
const overlayOpening = ref(false);
const overlayError = ref('');
const personId = ref('');
const category = ref('');
const rankingQuery = ref('');
const rankingWorkCategories = ref<string[] | null>(null);
const rankingDamageTypes = ref<DamageType[] | null>(null);
const selectedProfessions = ref<string[] | null>(null);
const damageGrouping = ref<RankingBoardFilter['damageGrouping']>('type');
const rankingSort = ref<RankingBoardFilter['sort']>('amount');
const rankingLimit = ref<number | null>(10);
const hideZero = ref(true);
const pausedEvents = shallowRef<{ events: OutputEvent[]; sessionId: string; gameDir: string } | null>(null);
const selectedPerson = shallowRef<{ personId: string; personName: string; person: PersonSnapshot | null; personSequence?: number | null; sessionId: string; gameDir: string } | null>(null);
const visuals = shallowRef<VisualCatalog | null>(null);
const detailVisuals = shallowRef<VisualCatalog | null>(null);
const visualsFailure = ref('');
const visualClient = createTowerClient(props.host);
let visualGeneration = 0;
let visualBusy = false;
const requestedIcons = new Set<string>();
const capture = computed(() => response.value?.capture);
const statistics = computed(() => response.value?.statistics);
const people = computed(() => statistics.value?.people ?? []);
const allWorkCategories = computed(() => statistics.value?.workCategories ?? []);
const allDamageTypes: DamageType[] = ['physical', 'magic', 'piercing', 'unknown'];
const professionOptions = computed(() => rankingProfessions(people.value));
const rankingFilter = computed<RankingBoardFilter>(() => ({ kind: kind.value, query: rankingQuery.value,
  workCategories: rankingWorkCategories.value, damageTypes: rankingDamageTypes.value, professions: selectedProfessions.value,
  damageGrouping: damageGrouping.value, sort: rankingSort.value, limit: rankingLimit.value, hideZero: hideZero.value }));
const rankingBoards = computed(() => buildRankingBoards(statistics.value, rankingFilter.value));
const shownPeople = computed(() => people.value.filter(row => (!personId.value || row.personId === personId.value)
  && (kind.value === 'work' ? row.work.length > 0 : row.damage.count > 0)));
const attackRows = computed(() => shownPeople.value.flatMap(person => person.damage.attacks.map(attack => ({ person, attack }))));
const shownEvents = computed(() => (pausedEvents.value?.events ?? statistics.value?.events ?? []).filter(row => row.kind === kind.value
  && (!personId.value || row.personId === personId.value) && (!category.value || row.category === category.value)).slice().reverse());
const detailListHint = computed(() => `${pausedEvents.value ? '明细显示已暂停，后台采集与汇总继续更新。' : '最多显示 500 条，并受容量限制；点击后固定当前明细。'}${shownEvents.value.length && shownEvents.value.every(event => event.detailSnapshot)
  ? '已显示明细附带原次公式快照，关闭后重开仍查看同一次事件。' : '未附公式快照的旧明细仍受后台保留容量限制。'}已选详情保留到手动关闭。`);
const hasActionCounts = computed(() => attackRows.value.some(row => row.attack.actionCount !== null));
const selectedEvent = computed(() => pinned.value?.event);
const formula = computed(() => pinned.value?.explanation?.formula);
const workFormula = computed(() => pinned.value?.explanation?.workFormula);
const detailPerson = computed(() => selectedPerson.value ?? (pinned.value ? { personId: pinned.value.event?.personId ?? pinned.value.summary.personId, personName: pinned.value.event?.personName ?? pinned.value.summary.personName, person: pinned.value.event?.person ?? null, personSequence: pinned.value.event?.person ? pinned.value.event.sequence : undefined, sessionId: pinned.value.sessionId, gameDir: pinned.value.gameDir } : null));
const damageTypeLabel = (value?: DamageType) => ({ physical: '力 · 物理', magic: '智 · 魔法', piercing: '敏 · 穿刺', unknown: '其他 / 未知' }[value ?? 'unknown']);
const attackKindLabel = (value?: AttackKind) => ({ basic: '普攻', skill: '技能', periodic: '持续伤害', unknown: '未分类' }[value ?? 'unknown']);
const isSelected = <T extends string,>(values: T[] | null, value: T) => values === null || values.includes(value);
function toggleSelection<T extends string>(values: T[] | null, value: T, available: T[]): T[] {
  const selected = new Set(values ?? available);
  if (selected.has(value)) selected.delete(value); else selected.add(value);
  return [...selected];
}
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
function freezeEvents() { return { events: clone(statistics.value?.events ?? []), sessionId: statistics.value?.sessionId ?? '', gameDir: props.gameDir.trim() }; }
function showEvent(event: OutputEvent) {
  selectedPerson.value = null;
  detailVisuals.value = visuals.value;
  if (!pausedEvents.value) pausedEvents.value = freezeEvents();
  pinEvent(event, pausedEvents.value);
}
function showLatestWork(row: PersonStatistics, cell: WorkCell) {
  selectedPerson.value = null;
  detailVisuals.value = visuals.value;
  pausedEvents.value = freezeEvents();
  pinLatest({ kind: 'work', personId: row.personId, category: cell.category }, { personName: row.personName, category: cell.category });
}
function showLatestAttack(row: PersonStatistics, cell: AttackCell) {
  selectedPerson.value = null;
  detailVisuals.value = visuals.value;
  pausedEvents.value = freezeEvents();
  pinLatest({ kind: 'damage', personId: row.personId, attackKind: cell.attackKind, skillId: cell.skillId },
    { personName: row.personName, category: cell.skillName || attackKindLabel(cell.attackKind) });
}
function showPerson(row: PersonStatistics) {
  closePinned();
  detailVisuals.value = visuals.value;
  selectedPerson.value = clone({ personId: row.personId, personName: row.personName, person: row.person, personSequence: row.personSequence, sessionId: statistics.value?.sessionId ?? '', gameDir: props.gameDir.trim() });
}
function retryIcons() { for (const key of requestedIcons) if (!visuals.value?.sprites[key]) requestedIcons.delete(key); visualsFailure.value = ''; void loadIcons(); }
function closeDetail() { closePinned(); selectedPerson.value = null; }
function toggleEvents() { pausedEvents.value = pausedEvents.value ? null : freezeEvents(); }
async function loadIcons() {
  if (visualBusy || !props.host.available || !props.gameDir.trim()) return;
  const snapshots = [...people.value.map(row => row.person), detailPerson.value?.gameDir === props.gameDir.trim() ? detailPerson.value.person : null];
  const keys = [...new Set(snapshots.flatMap(person => [person?.iconKey, ...(person?.equipment?.map(item => item.iconKey) ?? [])]).filter((key): key is string => Boolean(key)))].filter(key => !requestedIcons.has(key)).slice(0, 1024);
  if (!keys.length) return;
  const generation = visualGeneration;
  visualBusy = true;
  keys.forEach(key => requestedIcons.add(key));
  try {
    const result = await visualClient.visuals(props.gameDir, keys);
    if (generation !== visualGeneration) return;
    visuals.value = { schemaVersion: 1, sheets: { ...visuals.value?.sheets, ...result.sheets }, sprites: { ...visuals.value?.sprites, ...result.sprites } };
    if (detailPerson.value?.gameDir === props.gameDir.trim()) detailVisuals.value = visuals.value;
    visualsFailure.value = (result.warnings ?? []).join('；');
  } catch (error) {
    if (generation === visualGeneration) visualsFailure.value = `人物图标读取失败，仍显示人物名称：${errorMessage(error)}`;
  } finally { if (generation === visualGeneration) visualBusy = false; }
}
watch([people, detailPerson], () => { void loadIcons(); });
watch(() => props.gameDir, () => { visualGeneration++; visualBusy = false; requestedIcons.clear(); visuals.value = null; visualsFailure.value = ''; pausedEvents.value = null; });
onBeforeUnmount(() => { visualGeneration++; });
const canStart = computed(() => !pending.value && !failure.value && capture.value?.state === 'connected' && statistics.value?.state !== 'running');
const canStop = computed(() => !pending.value && statistics.value?.state === 'running');
const currentCapture = computed(() => !failure.value && props.host.available && props.gameDir.trim() ? capture.value : undefined);
const canInstall = computed(() => !pending.value && currentCapture.value?.installed === false && currentCapture.value.gameRunning === false && currentCapture.value.canInstall);
const canRemove = computed(() => !pending.value && currentCapture.value?.installed === true && currentCapture.value.gameRunning === false && currentCapture.value.canRemove);
const processLabel = computed(() => {
  if (!props.host.available) return '需要桌面应用';
  if (!props.gameDir.trim()) return '尚未指定游戏目录';
  if (currentCapture.value?.gameRunning === true) return '游戏运行中';
  if (currentCapture.value?.gameRunning === false) return '未检测到游戏进程';
  return '游戏进程状态待确认';
});
const captureMessage = computed(() => {
  if (!props.host.available) return '请在 OpenDesk 桌面应用中连接游戏。';
  if (!props.gameDir.trim()) return '请先在上方填写游戏安装目录；尚未检测游戏进程或采集入口。无需先载入存档。';
  if (failure.value) return '当前连接与安装状态待确认，请刷新后重试。';
  return capture.value?.message || '正在读取此目录的游戏进程与采集入口状态。';
});
const captureLabel = computed(() => {
  if (failure.value) return '连接状态待确认';
  if (!props.host.available) return '需要桌面应用';
  if (!props.gameDir.trim()) return '等待游戏目录';
  if (!capture.value) return '正在读取采集状态';
  return { 'not-installed': '未安装采集入口', waiting: '等待游戏连接', connected: '游戏已连接', stale: '连接已中断', error: '采集不可用' }[capture.value.state];
});
const recordingLabel = computed(() => {
  if (failure.value) return '上次结果 · 状态待确认';
  if (statistics.value?.state === 'running' && capture.value?.state !== 'connected') return '连接已断开 · 等待确认';
  return { idle: '尚未开始', running: '正在计时', stopped: '已停止', interrupted: '已中断' }[statistics.value?.state ?? 'idle'];
});
function number(value: number | null | undefined): string { return formatStatisticsOutputNumber(value, numberMode.value); }
function preciseNumber(value: number | null | undefined): string { return formatStatisticsNumber(value, numberMode.value); }
function percentage(value: number): string { return (value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 1 }); }
async function openOverlay() {
  if (overlayOpening.value) return;
  overlayError.value = '';
  if (!props.host.surface) { overlayError.value = '需升级宿主以支持透明窗口'; return; }
  if (!props.gameDir.trim()) { overlayError.value = '请先填写游戏安装目录。'; return; }
  overlayOpening.value = true;
  try {
    const context = { gameDir: props.gameDir, kind: kind.value, numberMode: numberMode.value, rankingFilter: clone(rankingFilter.value) };
    if (new TextEncoder().encode(JSON.stringify(context)).byteLength > 8 * 1024) throw new Error('排行筛选超过窗口上下文的 8 KiB 限制，请减少选择或缩短搜索后重试。');
    if (!parseRankingBoardFilter(context.rankingFilter)) throw new Error('排行筛选无效，请检查搜索文字和已选类型后重试。');
    await props.host.surface.open({ view: 'statistics', title: '工作与战斗排行', width: 440, height: 700,
      alwaysOnTop: true, context });
  } catch (error) { overlayError.value = `透明排行榜未能打开：${errorMessage(error)}`; }
  finally { overlayOpening.value = false; }
}
function duration(value = 0): string {
  const seconds = Math.max(0, Math.floor(value));
  return `${Math.floor(seconds / 3600).toString().padStart(2, '0')}:${Math.floor(seconds / 60 % 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}
async function action(operation: StatisticsOperation) {
  if (operation === 'statistics.start' && !canStart.value) return;
  if (operation === 'statistics.stop' && !canStop.value) return;
  if (operation === 'capture.install' && !canInstall.value) return;
  if (operation === 'capture.remove' && !canRemove.value) return;
  if (await request(operation)) {
    if (operation === 'statistics.start') { pausedEvents.value = null; personId.value = ''; category.value = ''; }
  }
}
watch(kind, () => {
  personId.value = ''; category.value = '';
  const allowedSorts = kind.value === 'work' ? ['amount', 'latest', 'count'] : ['amount', 'dps'];
  if (!allowedSorts.includes(rankingSort.value)) rankingSort.value = 'amount';
});
watch(() => statistics.value?.sessionId, () => { personId.value = ''; category.value = ''; });
</script>

<template>
  <section class="runtime-statistics" aria-label="工作与战斗统计">
    <section class="capture-panel">
      <div class="capture-heading"><Activity :size="18" /><div><h3>{{ captureLabel }}</h3><p>{{ captureMessage }}</p></div><span class="connection-badge" :class="{ connected: currentCapture?.state === 'connected' }">{{ processLabel }}</span></div>
      <p v-if="currentCapture?.updateRequired === true" class="capture-update">采集入口需更新：退出游戏后，在“采集入口管理”中先移除恢复原包，再安装此版本。</p>
      <details class="capture-management"><summary><Wrench :size="13" />采集入口管理<span v-if="currentCapture?.installed === true && currentCapture.updateRequired !== true">采集入口已安装，无需重复安装。</span></summary>
        <p v-if="capture?.coverage.length" class="coverage"><strong>当前采集范围</strong> {{ capture.coverage.join('；') }}</p>
        <div class="capture-installation">
          <div><p>安装会先备份当前游戏包，再向 <code>package.nw</code> 加入固定采集脚本。需先退出游戏，安装后重新启动游戏；当前运行的游戏不能直接附加。</p><p>移除会恢复安装前的原包，同样必须先退出游戏。不会由本工具关闭游戏。</p><p v-if="currentCapture?.updateRequired === true" class="update-status">更新前的新人物、类型和工作公式字段可能未采集。</p></div>
          <div class="installation-buttons"><button v-if="currentCapture?.installed === false" class="button button-secondary" :disabled="!canInstall" @click="action('capture.install')">{{ pending === 'capture.install' ? '正在安装…' : '安装采集入口（需重启游戏）' }}</button><button v-else-if="currentCapture?.installed === true" class="button button-secondary" :disabled="!canRemove" @click="action('capture.remove')">{{ pending === 'capture.remove' ? '正在恢复…' : '移除采集入口并恢复原包' }}</button><span v-else role="status">{{ gameDir.trim() ? '采集入口状态待确认' : '请先填写游戏安装目录' }}</span></div>
        </div>
      </details>
    </section>

    <div v-if="failure" class="status-warning" role="alert"><strong>刷新失败，显示上次成功结果。</strong><span>{{ failure }}</span><span>计时和累计不在页面推测；恢复连接后重新确认后台状态。</span></div>
    <div v-if="actionFailure" class="status-warning" role="alert"><strong>操作未确认成功</strong><span>{{ actionFailure }}</span><span>请根据采集状态检查结果，再重试所需操作。</span></div>
    <div v-if="statistics?.reason" class="status-warning" role="status">{{ statistics.reason }}</div>
    <section class="recording-panel" aria-label="计时控制">
      <div class="timer"><Clock3 :size="20" /><div><span>{{ recordingLabel }}</span><strong>{{ duration(statistics?.elapsedRealSeconds) }}</strong></div></div>
      <div class="recording-copy"><strong>从开始计时起累计全部事件</strong><p>新会话会清空上一轮结果。切换页签只暂停页面刷新，后台计时继续。</p><p>全会话 {{ number(statistics?.eventCount ?? 0) }} 次 · 最近更新 {{ updatedAt || '—' }} · 每秒读取后台状态</p></div>
      <div class="recording-actions"><button class="button button-primary" :disabled="!canStart" @click="action('statistics.start')"><Play :size="14" />{{ pending === 'statistics.start' ? '正在开始…' : '开始计时（新会话清零）' }}</button><button class="button button-secondary" :disabled="!canStop" @click="action('statistics.stop')"><Square :size="13" />停止计时</button><button class="refresh-button" aria-label="刷新采集与统计状态" :disabled="!!pending || !host.available || !gameDir.trim()" @click="action('statistics.status')"><RefreshCw :size="15" /></button></div>
    </section>

    <div class="statistics-toolbar">
      <div class="kind-tabs" aria-label="统计事件种类"><button :aria-pressed="kind === 'work'" @click="kind = 'work'">工作量</button><button :aria-pressed="kind === 'damage'" @click="kind = 'damage'">战斗伤害</button></div>
      <button class="button button-secondary" :disabled="overlayOpening" @click="openOverlay"><PanelsTopLeft :size="14" />{{ overlayOpening ? '正在打开…' : '透明排行榜' }}</button>
      <label class="display-mode">数值显示<select v-model="numberMode" aria-label="数值显示模式"><option value="raw">完整数字</option><option value="compact">中文单位 · 4位有效数字</option></select></label>
    </div>
    <p v-if="overlayError" class="status-warning" role="alert">{{ overlayError }}</p>
    <section class="output-cards" :class="{ 'work-summary': kind === 'work' }" aria-label="当前种类全局累计">
      <template v-if="kind === 'damage'"><div><span>完整伤害 · 全局累计</span><strong>{{ number(statistics?.summary.damage.amount ?? 0) }}</strong></div><div><span>每秒伤害 · DPS</span><strong>{{ number(statistics?.summary.damage.perRealSecond) }}</strong></div></template>
      <template v-else><p>累计实际工作量 <strong>{{ number(statistics?.summary.work.amount ?? 0) }}</strong></p><p>工作次数 <strong>{{ number(statistics?.summary.work.count ?? 0) }}</strong></p><small>记录真实输出：输出 100 万，即使任务仅需 1 万，也累计 100 万。</small></template>
    </section>
    <p v-if="visualsFailure" class="table-note" role="status">{{ visualsFailure }} <button class="close-detail" @click="retryIcons">重试图标</button></p>

    <section class="statistics-table-panel rankings-panel" aria-label="人物排行榜">
      <div class="section-heading"><div><h3>{{ kind === 'work' ? '工作分类排行' : '战斗伤害排行' }}</h3><p>{{ kind === 'work' ? '每种工作独立一榜；实际输出按会话累计，点击最近量查看读取时的最新公式。' : '选择伤害类型和职业，可合并或拆分榜单；DPS 使用本轮真实计时，包含游戏暂停期间。' }}</p></div></div>
      <div class="ranking-controls">
        <label class="ranking-search">搜索人物<input v-model="rankingQuery" type="search" maxlength="200" aria-label="搜索排行人物" placeholder="人物名称或编号" /></label>
        <label>排序<select v-model="rankingSort" aria-label="排行排序"><option value="amount">累计输出</option><option v-if="kind === 'work'" value="latest">最近一次实际量</option><option v-if="kind === 'work'" value="count">工作次数</option><option v-if="kind === 'damage'" value="dps">每秒伤害</option></select></label>
        <label>每榜人数<select v-model="rankingLimit" aria-label="每榜显示人数"><option :value="5">Top 5</option><option :value="10">Top 10</option><option :value="20">Top 20</option><option :value="null">全部</option></select></label>
        <label v-if="kind === 'damage'">分榜<select v-model="damageGrouping" aria-label="伤害分榜方式"><option value="combined">合并一个榜</option><option value="type">按伤害类型</option><option value="profession">按职业</option></select></label>
        <label class="ranking-checkbox"><input v-model="hideZero" type="checkbox" aria-label="隐藏零输出排行人物" />隐藏零输出</label>
      </div>
      <div v-if="kind === 'work'" class="ranking-choices" role="group" aria-label="排行工作类型多选"><span>工作类型</span><label v-for="name in allWorkCategories" :key="name" :class="{ checked: isSelected(rankingWorkCategories, name) }"><input type="checkbox" :checked="isSelected(rankingWorkCategories, name)" :aria-label="`排行工作：${name}`" @change="rankingWorkCategories = toggleSelection(rankingWorkCategories, name, allWorkCategories)" />{{ name }}</label><button class="close-detail" @click="rankingWorkCategories = null">全选工作</button><button class="close-detail" @click="rankingWorkCategories = []">清空工作</button></div>
      <div v-else class="ranking-choices" role="group" aria-label="排行伤害类型多选"><span>伤害类型</span><label v-for="type in allDamageTypes" :key="type" :class="{ checked: isSelected(rankingDamageTypes, type) }"><input type="checkbox" :checked="isSelected(rankingDamageTypes, type)" :aria-label="`排行伤害：${damageTypeLabel(type)}`" @change="rankingDamageTypes = toggleSelection(rankingDamageTypes, type, allDamageTypes)" />{{ damageTypeLabel(type) }}</label><button class="close-detail" @click="rankingDamageTypes = null">全选伤害</button><button class="close-detail" @click="rankingDamageTypes = []">清空伤害</button></div>
      <details class="ranking-professions"><summary>职业筛选 <span>{{ selectedProfessions === null ? '全部职业（含未知）' : `已选 ${selectedProfessions.length} 种` }}</span></summary><div class="ranking-choices" role="group" aria-label="排行职业多选"><label v-for="item in professionOptions" :key="item.value" :class="{ checked: isSelected(selectedProfessions, item.value) }"><input type="checkbox" :checked="isSelected(selectedProfessions, item.value)" :aria-label="`排行职业：${item.label}`" @change="selectedProfessions = toggleSelection(selectedProfessions, item.value, professionOptions.map(option => option.value))" />{{ item.label }}</label><button class="close-detail" @click="selectedProfessions = null">全选职业</button><button class="close-detail" @click="selectedProfessions = []">清空职业</button></div></details>
      <div class="ranking-grid">
        <article v-for="board in rankingBoards" :key="board.key" class="ranking-board" :aria-label="`${board.label}排行榜`" :data-board-key="board.key">
          <header class="ranking-board-heading"><div><h3>{{ board.label }}</h3><small>{{ board.peopleCount }} 人 · {{ rankingLimit === null ? '全部' : `Top ${rankingLimit}` }}</small></div><div><strong>{{ number(board.amount) }}</strong><small>本榜累计{{ kind === 'work' ? '工作量' : '伤害' }}</small></div></header>
          <div class="ranking-board-scroll"><table :aria-label="`${board.label}排行表`"><thead><tr><th>人物</th><template v-if="kind === 'work'"><th>最近实际量</th><th>工作次数</th><th>累计工作量</th></template><template v-else><th>完整伤害</th><th>DPS</th><th>占比</th></template></tr></thead><tbody><tr v-for="row in board.rows" :key="row.person.personId" :data-person-id="row.person.personId"><td><button class="person-button ranking-person" :aria-label="`查看${row.person.personName}人物详情`" @click="showPerson(row.person)"><span class="ranking-position">{{ row.rank }}</span><SpriteIcon :icon-key="row.person.person?.iconKey" :visuals="visuals" :label="row.person.personName" :size="26" fallback="person" /><span><strong>{{ row.person.personName }}</strong><small>{{ row.person.person?.profession || '职业未知' }}</small></span></button></td><template v-if="kind === 'work'"><td><button v-if="row.workCell" class="event-button work-value" :aria-label="`查看${row.person.personName}${board.workCategory}最近一次工作详情`" @click="showLatestWork(row.person, row.workCell)">{{ number(row.latestAmount) }}</button><span v-else class="no-value">—</span></td><td>{{ number(row.count) }}</td><td class="primary-number">{{ number(row.amount) }}</td></template><template v-else><td class="primary-number">{{ number(row.amount) }}</td><td>{{ number(row.dps) }}</td><td>{{ percentage(row.share) }}%</td></template></tr></tbody></table></div>
          <p v-if="!board.rows.length" class="empty-state">本榜暂无符合筛选的输出。</p><footer>显示 {{ board.rows.length }} / {{ board.peopleCount }} 人<span v-if="kind === 'work'"> · 最近量不等于平均量</span><span v-else> · 占比以本榜全部筛选结果为基准</span></footer>
        </article>
      </div>
      <p v-if="!rankingBoards.length" class="empty-state">暂无符合排行筛选的榜单。请检查已选类型、职业和搜索，或等待游戏产生事件。</p>
      <p class="table-note">排行筛选只影响上方榜单，不改变全局累计或下方明细。未采集的职业保留未知；伤害类型不会与普攻/技能命中分类交叉推算。</p>
    </section>

    <section class="statistics-table-panel detail-filters" aria-label="明细筛选">
      <div class="section-heading"><div><h3>明细筛选</h3><p>仅影响下方普攻/技能细表与逐次明细；与上方排行榜独立。</p></div><div class="filters"><select v-model="personId" aria-label="筛选统计人物"><option value="">全部明细人物</option><option v-for="person in people" :key="person.personId" :value="person.personId">{{ person.personName }} · {{ person.personId }}</option></select><select v-if="kind === 'work'" v-model="category" aria-label="筛选工作类型"><option value="">全部明细工作</option><option v-for="name in allWorkCategories" :key="name" :value="name">{{ name }}</option></select></div></div>
    </section>

    <section v-if="kind === 'damage'" class="statistics-table-panel" aria-label="普攻与技能统计">
      <div class="section-heading"><div><h3>普攻与技能</h3><p>次数与伤害分开看；多目标、多段命中不冒充一次施放。未捕获出手身份时只显示命中次数。</p></div></div>
      <div class="table-scroll"><table><thead><tr><th>人物</th><th>类型 / 技能</th><th v-if="hasActionCounts">出手次数</th><th>命中次数</th><th>完整伤害</th><th v-if="hasActionCounts">每次出手伤害</th><th>平均每次命中</th><th>最近一次命中</th></tr></thead><tbody><tr v-for="row in attackRows" :key="`${row.person.personId}:${row.attack.attackKind}:${row.attack.skillId}`"><td>{{ row.person.personName }}</td><td>{{ attackKindLabel(row.attack.attackKind) }}<small>{{ row.attack.skillName || (row.attack.skillId ? `编号 ${row.attack.skillId}` : '') }}</small></td><td v-if="hasActionCounts">{{ number(row.attack.actionCount) }}</td><td>{{ number(row.attack.count) }}</td><td class="primary-number">{{ number(row.attack.amount) }}</td><td v-if="hasActionCounts">{{ number(row.attack.averagePerAction) }}</td><td>{{ number(row.attack.averageAmount) }}</td><td><button class="event-button" @click="showLatestAttack(row.person, row.attack)">{{ number(row.attack.latestAmount) }}</button></td></tr></tbody></table></div>
      <p v-if="!attackRows.length" class="empty-state">尚无攻击记录。</p>
    </section>

    <section class="statistics-table-panel">
      <div class="section-heading"><div><h3>逐次明细 <span>{{ shownEvents.length }}</span></h3><p>{{ detailListHint }}</p></div><button class="button button-secondary" @click="toggleEvents">{{ pausedEvents ? '恢复明细刷新' : '暂停明细刷新' }}</button></div>
      <div class="table-scroll event-scroll"><table><thead><tr><th>序号</th><th>人物 / 类型</th><th>{{ kind === 'work' ? '本次实际工作量' : '本次完整伤害' }}</th><th>游戏时间</th><th>计时后收到</th></tr></thead><tbody><tr v-for="event in shownEvents" :key="event.sequence" :class="{ selected: pinned?.event?.sequence === event.sequence && pinned?.sessionId === (pausedEvents?.sessionId ?? statistics?.sessionId) }"><td><button class="event-button" :aria-label="`查看第 ${event.sequence} 次${event.kind === 'damage' ? '伤害' : '工作'}详情`" @click="showEvent(event)">#{{ event.sequence }}</button></td><td><strong>{{ event.personName }}</strong><small>{{ event.category }}<template v-if="event.kind === 'damage'"> · {{ damageTypeLabel(event.damageType) }}</template></small></td><td class="primary-number">{{ number(event.amount) }}</td><td>{{ preciseNumber(event.gameTime) }}</td><td>{{ preciseNumber(event.observedAfterSeconds) }} 秒</td></tr></tbody></table></div><p v-if="!shownEvents.length" class="empty-state">最近保留的明细中没有符合筛选的事件。</p>
    </section>

    <section v-if="detailPerson" class="event-detail detail-drawer" role="dialog" aria-label="固定详情" tabindex="-1" @keydown.esc.stop="closeDetail">
      <div class="detail-heading"><SpriteIcon :icon-key="detailPerson.person?.iconKey" :visuals="detailVisuals" :label="detailPerson.personName" :size="38" fallback="person" /><div><small>{{ selectedEvent ? `#${selectedEvent.sequence} · ${selectedEvent.category}` : pinned ? `${pinned.summary.category} · 最近一次` : '人物详情' }}</small><h3>{{ detailPerson.personName }}</h3></div><button class="close-detail" aria-label="关闭固定详情" @click="closeDetail">关闭详情</button></div>
      <p class="frozen-note">{{ pinned?.selectionMode === 'latest' ? selectedEvent ? '已固定读取时最近一次事件，刷新不会替换或关闭。' : '按人物和类型读取最近一次事件；成功前不显示序号或输出量。' : '已固定点击时的数据，刷新不会关闭。' }}<span v-if="detailPerson.sessionId !== statistics?.sessionId">来自上一计时会话。</span></p>
      <div class="detail-body"><p v-if="detailPerson.personSequence !== undefined && detailPerson.personSequence !== null" class="formula-note">人物资料来自采集事件 #{{ detailPerson.personSequence }}。</p>
        <dl class="person-facts"><dt>人物编号</dt><dd>{{ detailPerson.personId }}</dd><dt>职业</dt><dd>{{ detailPerson.person?.profession || '未采集' }}</dd><dt>当前工作</dt><dd>{{ detailPerson.person?.job || '未采集' }}</dd><dt>外观</dt><dd>{{ detailPerson.person?.appearance || '未采集' }}</dd></dl>
        <p v-if="detailPerson.person?.professionDescription" class="formula-note">{{ detailPerson.person.professionDescription }}</p>
        <p v-if="!detailPerson.person && (!pinned || selectedEvent)" class="formula-note">这条记录没有人物快照；更新采集入口后，新事件会带上可核实的人物信息。</p>
        <div v-if="detailPerson.person?.attributes?.length" class="term-grid person-attributes"><div v-for="attribute in detailPerson.person.attributes" :key="attribute.id"><span>{{ attribute.label }}</span><strong>{{ preciseNumber(attribute.value) }}</strong></div></div>
        <ul v-if="detailPerson.person?.equipment?.length" class="equipment-list"><li v-for="(item, index) in detailPerson.person.equipment" :key="index"><SpriteIcon :icon-key="item.iconKey" :visuals="detailVisuals" :label="item.name" :size="24" fallback="item" /><span>{{ item.slot }} · {{ item.name }}<small>{{ item.type }} {{ item.level === undefined ? '' : `${item.level} 级` }} {{ item.qualityName }}</small></span></li></ul>
        <template v-if="pinned">
          <div v-if="selectedEvent" class="selected-output"><span>{{ selectedEvent.kind === 'work' ? '本次实际工作量' : '本次完整伤害' }}</span><strong>{{ number(selectedEvent.amount) }}</strong></div>
          <p v-if="selectedEvent?.kind === 'damage'" class="formula-note">{{ attackKindLabel(selectedEvent.attackKind) }} · {{ damageTypeLabel(selectedEvent.damageType) }}<template v-if="selectedEvent.skillName"> · {{ selectedEvent.skillName }}</template></p>
          <p v-if="pinned.loading" class="formula-note">{{ pinned.latest ? '正在读取该人物/类型最近一次事件及公式…' : '正在读取本次事件的计算公式…' }}</p>
          <div v-else-if="pinned.error" class="status-warning" role="alert"><span>{{ pinned.error }}</span><button class="close-detail" @click="explainPinned">重试公式读取</button></div>
          <template v-else-if="selectedEvent?.kind === 'work' && workFormula">
            <p class="formula-expression">{{ workFormula.text }}</p><div class="formula-check" :class="{ mismatch: !workFormula.matches }"><strong>{{ workFormula.matches ? '本次工作复算一致' : '本次工作无法复算' }}</strong><span>实际 {{ number(workFormula.result) }}</span><span>复算 {{ number(workFormula.calculated) }}</span></div>
            <ol v-if="workFormula.steps?.length" class="formula-steps"><li v-for="(step, index) in workFormula.steps" :key="index"><span>{{ step.label }}</span><strong>{{ preciseNumber(step.value) }}</strong></li></ol>
            <details class="formula-inputs" open><summary>展开工作公式原始项（{{ workFormula.terms.length }} 项）</summary><div class="term-grid"><div v-for="term in workFormula.terms" :key="term.id"><span>{{ term.label }}</span><strong>{{ term.value }}</strong></div></div></details><p class="formula-note">{{ workFormula.limitation }}</p>
          </template>
          <template v-else-if="selectedEvent?.kind === 'damage' && formula">
            <p class="formula-expression">{{ formula.text }}</p>
            <div class="formula-check" :class="{ mismatch: !formula.matches }"><strong>{{ formula.matches ? '本击复算一致' : '本击无法复算 · 不展示提升比较' }}</strong><span>游戏结果 {{ number(formula.result) }}</span><span>公式复算 {{ number(formula.calculated) }}</span></div>
            <details class="formula-inputs"><summary>展开公式原始项（{{ formula.inputs.length }} 项）</summary><div class="term-grid"><div v-for="(term, index) in formula.terms" :key="term.id"><span>{{ index + 1 }}. {{ term.label }}</span><strong>{{ term.value }}</strong><small>{{ term.mode === 'multiplier' ? '乘区' : term.mode === 'additive' ? '加算增伤' : '防御率' }}</small></div></div></details>
            <template v-if="formula.matches"><h4>改变一项后，本击输出的变化</h4><p class="formula-note">按本击收益排序。各行改变幅度不同，并非相同投入成本的比较。</p><div class="table-scroll"><table><thead><tr><th>公式项</th><th>改变幅度</th><th>调整后输出</th><th>增加输出</th><th>增幅</th></tr></thead><tbody><tr v-for="row in formula.sensitivity" :key="row.id"><td>{{ row.label }}</td><td>{{ row.changeLabel }}</td><td>{{ number(row.adjusted) }}</td><td class="primary-number">{{ number(row.delta) }}</td><td>{{ row.gainPercent === null ? '未知' : `${preciseNumber(row.gainPercent)}%` }}</td></tr></tbody></table></div></template><p class="formula-note">{{ formula.limitation }}</p>
          </template>
          <p v-else class="formula-note">{{ pinned.explanation?.message || '这条记录没有可复算的完整公式。' }}</p>
        </template>
      </div>
    </section>
  </section>
</template>

<style scoped>
.runtime-statistics { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.capture-panel, .recording-panel, .statistics-table-panel, .event-detail { border: 1px solid var(--border); border-radius: 9px; background: var(--surface); overflow: hidden; }
.capture-heading { display: flex; align-items: center; gap: 12px; padding: 12px 16px; color: var(--accent); }
h3 { font-size: 13px; font-weight: 550; color: var(--text); margin: 0; }
p { margin: 0; line-height: 1.8; }
.capture-heading p { color: var(--muted); font-size: 11px; margin-top: 5px; }
.connection-badge { flex-shrink: 0; margin-left: auto; border: 1px solid var(--border); color: var(--muted); border-radius: 20px; font-size: 10px; padding: 5px 9px; }
.connection-badge.connected { color: var(--success-text); background: var(--success-bg); }
.coverage { color: var(--muted); font-size: 10px; margin: 0 18px 14px; }
.coverage strong { color: var(--text); font-weight: 500; margin-right: 7px; }
.capture-management { border-top: 1px solid var(--border); }
.capture-management summary { display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--muted); padding: 8px 16px; font-size: 11px; }
.capture-management summary > span { margin-left: auto; color: var(--success-text); font-size: 10px; }
.capture-update { color: var(--error-text); font-size: 11px; padding: 0 16px 10px; }
.capture-installation { display: flex; align-items: center; gap: 12px; padding: 14px 18px; background: var(--surface-soft); border-top: 1px solid var(--border); color: var(--muted); font-size: 10px; }
.capture-installation > svg { flex-shrink: 0; }
.capture-installation > div:first-of-type { flex: 1; }
.installation-buttons { flex-shrink: 0; }
.installed-status { color: var(--success-text); font-weight: 500; }
.status-warning { display: flex; flex-direction: column; gap: 5px; border: 1px solid var(--border); border-left: 3px solid var(--error-text); color: var(--error-text); background: var(--surface); border-radius: 6px; padding: 12px 16px; font-size: 11px; }
.recording-panel { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; padding: 12px 16px; }
.timer { display: flex; flex: 0 0 auto; align-items: center; gap: 12px; color: var(--accent); }
.timer span { display: block; color: var(--muted); font-size: 10px; margin-bottom: 6px; }
.timer strong { color: var(--text); font-size: 24px; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
.recording-copy { flex: 1 1 240px; min-width: min(100%, 220px); max-width: 100%; font-size: 10px; color: var(--muted); }
.recording-copy > strong { color: var(--text); font-size: 11px; font-weight: 500; }
.recording-copy p { margin-top: 4px; }
.recording-actions { display: flex; flex: 0 1 auto; flex-wrap: wrap; min-width: 0; max-width: 100%; gap: 7px; align-items: center; }
.recording-actions .button { flex: 0 0 auto; max-width: 100%; white-space: nowrap; }
.refresh-button { display: grid; flex: 0 0 32px; place-items: center; width: 32px; height: 32px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--muted); }
button { cursor: pointer; }
button:disabled { opacity: .48; cursor: not-allowed; }
button:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.statistics-toolbar { display: flex; flex-wrap: wrap; min-width: 0; align-items: center; gap: 10px 15px; justify-content: space-between; }
.statistics-toolbar > .button { flex: 0 0 auto; max-width: 100%; white-space: nowrap; }
.statistics-toolbar p { color: var(--muted); font-size: 10px; }
.kind-tabs { display: inline-flex; flex-shrink: 0; gap: 3px; padding: 3px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 6px; }
.kind-tabs button { border: 0; background: none; color: var(--muted); border-radius: 4px; padding: 7px 15px; font-size: 11px; }
.kind-tabs button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
.output-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.output-cards > div { display: flex; flex-direction: column; gap: 8px; padding: 17px 18px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.output-cards span, .output-cards small { color: var(--muted); font-size: 10px; }
.output-cards strong { font-size: 25px; font-weight: 500; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.output-cards > div:first-child strong { color: var(--accent); }
.section-heading { display: flex; flex-wrap: wrap; min-width: 0; align-items: center; justify-content: space-between; gap: 15px; padding: 12px 16px; }
.section-heading > div:first-child { flex: 1 1 240px; min-width: min(100%, 220px); }
.section-heading > .button { flex: 0 0 auto; max-width: 100%; white-space: nowrap; }
.section-heading h3 span { color: var(--muted); font-size: 10px; font-weight: 400; margin-left: 6px; }
.section-heading p { color: var(--muted); font-size: 10px; margin-top: 6px; }
.filters { display: flex; flex-wrap: wrap; min-width: 0; max-width: 100%; gap: 8px; }
.filters select { min-width: min(110px, 100%); max-width: min(220px, 100%); }
select { max-width: 220px; min-width: 110px; min-height: 33px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface-soft); color: var(--text); font-size: 10px; }
.table-scroll { overflow: auto; scrollbar-width: thin; }
.event-scroll { max-height: 360px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
th { background: var(--surface-soft); color: var(--muted); font-weight: 450; white-space: nowrap; position: sticky; top: 0; z-index: 1; }
th, td { padding: 11px 15px; text-align: right; border-top: 1px solid var(--border); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
td strong { font-weight: 500; }
td small { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; }
.primary-number { color: var(--accent); font-weight: 550; }
.empty-state { text-align: center; padding: 28px 18px; color: var(--muted); font-size: 11px; }
.table-note { border-top: 1px solid var(--border); padding: 10px 18px; color: var(--muted); font-size: 10px; }
.event-button { border: 1px solid var(--border); border-radius: 4px; background: var(--surface-soft); color: var(--accent); padding: 5px 8px; font: inherit; }
tr.selected { background: var(--accent-soft); }
.close-detail { border: 0; background: transparent; padding: 4px 0; color: var(--accent); font-size: 10px; white-space: nowrap; }
.event-detail { padding-bottom: 8px; }
.formula-note { color: var(--muted); padding: 8px 18px 12px; font-size: 10px; }
.formula-expression { margin: 0 18px 12px; padding: 14px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 6px; font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
.formula-check { display: flex; flex-wrap: wrap; gap: 10px 20px; margin: 0 18px 14px; font-size: 11px; color: var(--muted); }
.formula-check strong { color: var(--success-text); font-weight: 500; }
.formula-check.mismatch strong { color: var(--error-text); }
.formula-inputs { margin: 0 18px 14px; font-size: 11px; }
.formula-inputs summary { cursor: pointer; color: var(--accent); }
.term-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }
.term-grid > div { display: flex; flex-wrap: wrap; gap: 7px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 5px; padding: 10px; }
.term-grid span { flex-basis: 100%; color: var(--muted); font-size: 10px; }
.term-grid strong { font-weight: 500; }
.term-grid small { margin-left: auto; color: var(--muted); }
h4 { font-size: 12px; font-weight: 500; margin: 16px 18px 0; }
/* Keep selected content outside tables whose rolling rows are replaced. */
.work-summary { display: flex; flex-wrap: wrap; gap: 12px 24px; align-items: center; padding: 12px 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-soft); color: var(--muted); font-size: 11px; }
.work-summary strong { color: var(--text); font-weight: 550; margin-left: 8px; }
.work-summary > small { flex-basis: 100%; font-size: 10px; }
.display-mode { display: flex; flex: 0 1 auto; flex-wrap: wrap; min-width: 0; max-width: 100%; align-items: center; gap: 8px; color: var(--muted); font-size: 10px; }
.display-mode select { min-width: 0; max-width: 100%; }
.summary-scroll { max-height: 500px; }
.summary-scroll th:first-child, .summary-scroll td:first-child { position: sticky; left: 0; background: var(--surface); z-index: 2; }
.summary-scroll th:first-child { z-index: 3; background: var(--surface-soft); }
.person-button { display: flex; align-items: center; gap: 9px; padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; }
.person-button:hover strong { color: var(--accent); }
.person-cell { min-width: 130px; }
.work-cell { border: 1px solid transparent; background: transparent; color: var(--text); font: inherit; text-align: right; min-width: 112px; padding: 3px 7px; border-radius: 5px; }
.work-cell:hover:enabled { border-color: var(--border); background: var(--accent-soft); }
.work-cell > strong { display: block; color: var(--accent); font-size: 13px; }
.work-cell > span { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; }
.work-cell:disabled { opacity: 1; cursor: default; }
.no-value { color: var(--muted); }
.update-status { color: var(--error-text); font-weight: 500; }
.detail-drawer { position: fixed; top: 12px; bottom: 12px; right: 12px; z-index: 30; width: min(620px, calc(100vw - 24px)); display: flex; flex-direction: column; padding: 0; box-shadow: 0 12px 48px rgb(0 0 0 / 22%); }
.detail-heading { display: flex; align-items: center; gap: 12px; padding: 18px; border-bottom: 1px solid var(--border); background: var(--surface); flex-shrink: 0; }
.detail-heading > div { flex: 1; }
.detail-heading small { color: var(--muted); font-size: 10px; display: block; margin-bottom: 5px; }
.detail-heading .close-detail { padding: 6px 9px; border: 1px solid var(--border); border-radius: 5px; }
.frozen-note { padding: 8px 18px; background: var(--accent-soft); color: var(--accent); font-size: 10px; flex-shrink: 0; }
.detail-body { overflow: auto; overscroll-behavior: contain; padding-bottom: 16px; scrollbar-width: thin; }
.person-facts { display: grid; grid-template-columns: 70px minmax(0, 1fr) 70px minmax(0, 1fr); gap: 10px; padding: 16px 18px; margin: 0; font-size: 11px; }
.person-facts dt { color: var(--muted); }
.person-facts dd { margin: 0; overflow-wrap: anywhere; }
.person-attributes { padding: 0 18px 12px; }
.selected-output { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px; border-top: 1px solid var(--border); font-size: 11px; color: var(--muted); }
.selected-output strong { color: var(--accent); font-size: 24px; font-weight: 500; font-variant-numeric: tabular-nums; }
.formula-steps { list-style: decimal; padding: 0 18px 0 38px; font-size: 11px; }
.formula-steps li { padding: 7px 0; }
.formula-steps strong { float: right; margin-left: 12px; font-weight: 500; }
.equipment-list { list-style: none; padding: 0 18px 12px; display: grid; gap: 7px; }
.equipment-list li { display: flex; gap: 8px; align-items: center; font-size: 11px; }
.equipment-list small { display: block; color: var(--muted); font-size: 10px; margin-top: 3px; }
.ranking-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 0 16px 12px; }
.ranking-controls > label { display: flex; flex-wrap: wrap; min-width: 0; max-width: 100%; align-items: center; gap: 7px; font-size: 11px; color: var(--muted); white-space: nowrap; }
.ranking-controls input[type="search"] { box-sizing: border-box; min-width: min(100px, 100%); max-width: 100%; width: 185px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 5px; color: var(--text); background: var(--surface-soft); font: inherit; }
.ranking-controls select { min-width: min(92px, 100%); max-width: 100%; padding: 6px 8px; font-size: 11px; }
.ranking-controls .ranking-checkbox { margin-left: auto; }
.ranking-choices { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; padding: 0 16px 12px; font-size: 11px; color: var(--muted); }
.ranking-choices > span { margin-right: 4px; }
.ranking-choices label { display: inline-flex; align-items: center; gap: 5px; padding: 4px 7px; border-radius: 5px; border: 1px solid var(--border); background: var(--surface-soft); cursor: pointer; }
.ranking-choices label.checked { color: var(--text); border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); }
.ranking-choices input, .ranking-controls input[type="checkbox"] { margin: 0; accent-color: var(--accent); }
.ranking-professions { margin: 0 0 12px; font-size: 11px; color: var(--muted); }
.ranking-professions summary { margin: 0 16px; cursor: pointer; }
.ranking-professions summary span { margin-left: 8px; }
.ranking-professions[open] .ranking-choices { padding-top: 9px; padding-bottom: 0; }
.ranking-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr)); align-items: start; gap: 12px; padding: 0 14px 14px; }
.ranking-board { min-width: 0; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; background: var(--surface); }
.ranking-board-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 11px 12px 9px; }
.ranking-board-heading > div:last-child { text-align: right; min-width: 0; }
.ranking-board-heading h3 { font-size: 14px; }
.ranking-board-heading strong { font-size: 14px; font-weight: 550; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.ranking-board-heading small { display: block; margin-top: 3px; font-size: 10px; color: var(--muted); }
.ranking-board-scroll { max-height: 470px; overflow: auto; scrollbar-width: thin; }
.ranking-board table { table-layout: fixed; }
.ranking-board th, .ranking-board td { white-space: normal; overflow-wrap: anywhere; padding: 9px 7px; font-size: 12px; }
.ranking-board th { font-size: 10px; }
.ranking-board th:first-child { width: 34%; }
.ranking-board th:nth-child(3) { width: 15%; }
.ranking-board td:first-child, .ranking-board th:first-child { padding-left: 10px; }
.ranking-board .ranking-person { width: 100%; min-width: 0; gap: 5px; font: inherit; }
.ranking-person > span:last-child { min-width: 0; }
.ranking-person strong, .ranking-person small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ranking-person strong { font-size: 12px; }
.ranking-person small { font-size: 10px; margin-top: 2px; }
.ranking-position { color: var(--muted); min-width: 10px; flex-shrink: 0; font-size: 10px; }
.ranking-board .work-value { max-width: 100%; border: 0; padding: 3px 0; background: transparent; text-align: right; white-space: normal; overflow-wrap: anywhere; text-decoration: underline; text-underline-offset: 3px; }
.ranking-board .work-value:hover { color: var(--text); }
.ranking-board footer { padding: 7px 11px; border-top: 1px solid var(--border); color: var(--muted); font-size: 10px; }
.rankings-panel > .table-note { margin: 0; }
.ranking-controls input:focus-visible, .ranking-professions summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
@media (max-width: 1100px) { .recording-panel, .capture-installation { flex-wrap: wrap; } .recording-actions { flex-basis: 100%; } .capture-installation > div:first-of-type { min-width: 65%; } .installation-buttons { margin-left: 27px; } .statistics-toolbar, .section-heading { align-items: flex-start; flex-wrap: wrap; } }
@media (max-width: 700px) { .term-grid { grid-template-columns: 1fr 1fr; } .connection-badge { display: none; } .recording-copy { flex-basis: 100%; } .filters { flex-wrap: wrap; } .person-facts { grid-template-columns: 75px minmax(0, 1fr); } }
</style>
