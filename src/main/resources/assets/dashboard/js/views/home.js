/**
 * home.js — 首頁 view（2026-10-21 由「今日」與「總覽」合併）
 *
 * 固定看「現在」，不吃頂列的時間範圍——會隨範圍變動的走勢都在趨勢頁。
 * 6 個區塊，依「回答什麼問題」分（分類見 docs/dev/FRONTEND_REFACTOR.md 階段 6）：
 *   綜覽（今日課表＋訓練完備程度／本週訓練狀況＋賽事倒數）、訓練負荷、體能狀況、
 *   當週課表、戰力綜覽、近期活動
 * 區塊的順序與顯隱由使用者自訂（core/customize.js，偏好 key 'home'）。
 *
 * 純呈現層：把三欄位事實（數值/來源/信賴度）與課表原樣顯示出來，
 * 不做任何「今天該不該練」的判讀——判讀留給 AI 教練（見 PLAN.md §7.3、
 * mcp/server.py 的 coach 行為約束）。各指標的 ⓘ 只寫定義與算法。
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, DAY_STATE_LABEL, CONFIDENCE_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError, showUnavailable } from '../core/dom.js';
import { confBadge, kpiCard, infoTip, closeInfoPopover } from '../core/facts.js';
import { addDaysISO, todayISO, dowLabel, planSummary, shortPlanNote } from '../core/format.js';
import { customizer, layoutOf } from '../core/customize.js';
import { loadPlanWeek, planWeekLabel, renderPlanWeek, planWeekTile, planWeekTileValue, planWeekSummary } from './plan-week.js';
import { activityDots, firstRunningActivity } from './calendar.js';
import { renderSettingsSync } from './settings.js';
import { zoneSplitBar } from './overview.js';
import { renderGarminWall, renderGarminCard } from './garmin.js';
import { navigate } from '../shell/router.js';
import { renderSidebarSync } from '../shell/sync.js';

// ------------------------------------------------------------ 區塊版面

var HOME_BLOCKS = [
  { key: 'today', label: '綜覽' },
  { key: 'load', label: '訓練負荷' },
  { key: 'body', label: '體能狀況' },
  { key: 'week', label: '當週課表' },
  { key: 'ability', label: '戰力綜覽' },
  { key: 'recent', label: '近期活動' }
];

/**
 * 依使用者偏好重排區塊（移動既有 DOM，不重畫內容）並套用顯隱。
 * 任何區塊都能並排：可見區塊由上而下兩兩一組，各佔半欄；最後落單的佔滿整欄，
 * 避免半邊空白。區塊內容依自身寬度（container query）調整欄數，半欄也排得下。
 */
export function applyHomeLayout() {
  var grid = $('home-grid');
  if (!grid) return;
  var layout = layoutOf('home', HOME_BLOCKS);
  var visible = [];
  layout.ordered.forEach(function (b) {
    var el = $('block-' + b.key);
    if (!el) return;
    grid.appendChild(el);
    el.hidden = !!layout.hidden[b.key];
    el.classList.remove('is-half');
    if (!el.hidden) visible.push(b);
  });
  for (var i = 0; i + 1 < visible.length; i += 2) {
    $('block-' + visible[i].key).classList.add('is-half');
    $('block-' + visible[i + 1].key).classList.add('is-half');
  }
}

function mountHomeCustomizer() {
  var node = $('home-customize');
  if (!node) return;
  clear(node);
  node.appendChild(customizer({
    settingsKey: 'home',
    defs: HOME_BLOCKS,
    label: '⚙ 自訂首頁',
    hint: '調整區塊順序，取消勾選可隱藏。區塊由上而下兩兩並排，落單的佔滿整欄。',
    onChange: function () {
      applyHomeLayout();
      mountHomeCustomizer();
    }
  }));
}

/** 啟動時呼叫一次：套用區塊順序並放上「自訂首頁」鈕。 */
export function initHomeLayout() {
  applyHomeLayout();
  mountHomeCustomizer();
}

// ------------------------------------------------------------ 今天：課表＋賽事倒數＋訓練完備程度

/**
 * 一天的課表：課別＋距離、配速（粗體），後面接教練原文的精簡版（淡色，完整原文在 title）。
 * 休息日只顯示「休息」——備註只是「Rest」之類的重述（與 plan-week.js 一致）。
 */
export function renderTodayPlanRow(label, plan) {
  var row = h('div', 'today-plan-row');
  row.appendChild(h('span', 'today-plan-label', label));
  if (!plan) {
    row.appendChild(h('span', 'today-plan-value', '無課表'));
    return row;
  }
  var text = (WORKOUT_TYPE_LABEL[plan.workout_type] || plan.workout_type || '—');
  if (plan.planned_distance_km !== null && plan.planned_distance_km !== undefined && plan.planned_distance_km > 0) {
    text += ' ' + C.formatNumber(plan.planned_distance_km, 1) + ' km';
  }
  row.appendChild(h('span', 'today-plan-value', text));
  if (plan.planned_pace_sec_per_km) {
    row.appendChild(h('span', 'today-plan-value', C.formatPace(plan.planned_pace_sec_per_km) + '/km'));
  }
  var note = plan.workout_type === 'rest' ? '' : shortPlanNote(plan.notes);
  // 教練原文開頭的「課程日」只是標籤，移到句尾，讓課表內容緊接在配速後面
  var tag = note.match(/^課程日\s*/);
  if (tag) note = note.slice(tag[0].length) + ' 課程日';
  if (note) {
    var n = h('span', 'today-plan-note', note);
    n.title = plan.notes;
    row.appendChild(n);
  }
  return row;
}

/** 課表標題：「課表（於 YYYY-MM-DD 更新）」，取今日或明日課表的建立日期。 */
function planBlockTitle(planned) {
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '課表'));
  var source = planned.today || planned.tomorrow;
  if (source && source.created_at) {
    title.appendChild(h('span', 'today-note', '（於' + source.created_at.slice(0, 10) + '更新）'));
  }
  return title;
}

// 判定 → 顯示。顏色之外一律附圖示與文字，不單靠顏色（色盲、列印也看得懂）。
var READINESS_STATE = {
  normal: { cls: 'ok', icon: '✓', text: '正常' },
  low: { cls: 'low', icon: '▼', text: '偏低' },
  none: { cls: 'none', icon: '–', text: '未評' }
};

function readinessStateOf(value) {
  return READINESS_STATE[value === 'low' || value === 'normal' ? value : 'none'];
}

function readinessPill(value) {
  var s = readinessStateOf(value);
  var pill = h('span', 'readiness-pill readiness-' + s.cls);
  pill.appendChild(h('span', 'readiness-pill-icon', s.icon));
  pill.appendChild(h('span', null, s.text));
  return pill;
}

var READINESS_TIPS = [
  '綜合訓練完備程度是本專案規則引擎（公開、可重現的規則，不是 AI 判讀）'
    + '對「今天身體恢復得夠不夠完備」的判定。顏色一看就懂：'
    + '綠色 ✓ 正常（查過了，沒有異常訊號）；紅色 ▼ 偏低（至少一個維度觸發）；'
    + '灰色 – 未評（三個維度都缺資料、無法評估，不等於正常）。',
  '三個維度，任一觸發就判為「偏低」：①連續訓練天數達閾值（預設 6 天，可個人化）；'
    + '②TSB 低於你自己歷史分布的第 10 百分位；③當日 HRV 較 7 日均值下降 15% 以上。',
  '各觸發項旁的標籤是信賴度：連續天數只是數活動日，可信；TSB 取決於最大心率是否實測；'
    + 'HRV 的百分比門檻尚未經文獻驗證，標為低。',
  '資料不足的維度會跳過（例如 HRV 近 7 天少於 3 筆、當天尚未同步）。',
  '這裡只給旗標與原因，不改課表也不給建議；要不要調整交給 AI 教練。'
    + '與 Garmin 的「訓練完備程度」是兩回事，後者是 Garmin 數據，不納入本專案判斷。'
];

/** 今天還沒同步、改顯示較早一天資料時的說明文字。 */
function unsyncedNote(dateStr) {
  return '今日資料尚未同步，以下為 ' + C.shortDate(dateStr) + '（' + dowLabel(dateStr) + '）的資料';
}

/** 帶正負號的數字（正數補「+」），TSB 與 HRV 變化用。 */
function signedNumber(v, decimals) {
  return (v > 0 ? '+' : '') + C.formatNumber(v, decimals);
}

// 三個判斷維度，依「數值 / 觸發門檻」並列（與本週訓練狀況的「實際 / 課表」同一種讀法）。
// key 對應後端 readiness 的 measurements，dim 對應 triggers／not_assessed 的 dimension。
var READINESS_ROWS = [
  { dim: 'consecutive_training_days', key: 'consecutive_training_days', label: '連續訓練',
    fmt: function (v) { return String(v); }, unit: ' 天' },
  { dim: 'tsb', key: 'tsb', label: 'TSB',
    fmt: function (v) { return signedNumber(v, 1); }, unit: '' },
  { dim: 'hrv', key: 'hrv_change_pct', label: 'HRV',
    fmt: function (v) { return signedNumber(v, 1) + '%'; }, unit: '' }
];

/** 一列「數值 / 門檻」。觸發的維度整列標紅並附信賴度；未評的顯示「—」，滑鼠移上去看原因。 */
function readinessRow(def, entry) {
  var m = entry && entry.measurements && entry.measurements[def.key];
  var trigger = entry && (entry.triggers || []).filter(function (t) { return t.dimension === def.dim; })[0];
  var skipped = entry && (entry.not_assessed || []).filter(function (na) { return na.dimension === def.dim; })[0];
  var hasValue = m && m.value !== null && m.value !== undefined;

  var row = h('div', 'wt-row rd-row' + (trigger ? ' is-triggered' : ''));
  row.appendChild(h('span', 'wt-row-label', def.label));
  var val = h('span', 'wt-row-value');
  val.appendChild(h('span', 'rd-value', hasValue ? def.fmt(m.value) : '—'));
  if (hasValue && m.threshold !== null && m.threshold !== undefined) {
    val.appendChild(h('span', 'wt-planned', '/ ' + def.fmt(m.threshold) + def.unit));
  } else if (hasValue && def.unit) {
    val.appendChild(h('span', 'wt-planned', def.unit.trim()));
  }
  row.appendChild(val);
  if (trigger) {
    var level = (trigger.confidence && trigger.confidence.level) || 'high';
    row.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
    row.title = trigger.detail;
  } else if (skipped) {
    row.title = skipped.reason;
  }
  return row;
}

/** 右側的判定圖示：實心圓＋符號，下方寫判定文字。 */
function readinessStatus(value) {
  var s = readinessStateOf(value);
  // 狀態文字（兩個字）直接放在圓裡，圓下方不再多佔一行
  var badge = h('span', 'rd-status-icon readiness-' + s.cls, s.text);
  badge.title = s.cls === 'none' ? '三個維度都缺資料，無法評估（不等於正常）' : '綜合訓練完備程度：' + s.text;
  return badge;
}

export function renderTodayReadinessBlock(readiness) {
  var block = h('div', 'today-block readiness-block fig-block');
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '綜合訓練完備程度'));
  title.appendChild(infoTip('綜合訓練完備程度', READINESS_TIPS));
  block.appendChild(title);

  var entry = readiness && readiness.today;
  var fallback = readiness && readiness.today_fallback;
  var todayMissing = !entry || entry.readiness === null || entry.readiness === undefined;
  var unsynced = false;
  if (todayMissing && fallback) {
    // 今天還沒同步：先呈現退回窗口內最近一天的判定，日期標在標題旁、說明放在卡片下方
    unsynced = true;
    entry = fallback;
    todayMissing = false;
  }
  var shownDate = entry && entry.date ? entry.date : (state.today && state.today.date);
  if (shownDate) title.appendChild(h('span', 'today-note fig-title-date', C.shortDate(shownDate) + ' (' + dowLabel(shownDate) + ')'));

  // 左側三個維度的數值 / 門檻、右側判定圖示
  var card = h('div', 'wt-card');
  var rows = h('div', 'wt-figs');
  READINESS_ROWS.forEach(function (def) { rows.appendChild(readinessRow(def, todayMissing ? null : entry)); });
  card.appendChild(rows);
  card.appendChild(readinessStatus(todayMissing ? null : entry.readiness));
  block.appendChild(card);

  if (unsynced) block.appendChild(h('div', 'sync-fallback-note', '今日資料尚未同步'));
  if (todayMissing) {
    var reasons = (entry && entry.not_assessed || []).map(function (na) { return na.reason; });
    block.appendChild(h('div', 'today-note wt-note', reasons.length ? reasons[0] : '今日尚無足夠資料可評估'));
    var latest = readiness && readiness.latest_evaluable;
    if (latest) {
      var lrow = h('div', 'today-note');
      lrow.appendChild(document.createTextNode('最近一次可評估：' + latest.date + '　'));
      lrow.appendChild(readinessPill(latest.readiness));
      block.appendChild(lrow);
    }
  }
  return block;
}

/** 「下場賽事」「目標賽事」剩餘天數。賽事資料來自學員檔（後端 races）。 */
// 單色線條圖示，用 currentColor 跟著文字變色。內容是固定的字面值，不含任何外部資料。
var RACE_ICON_SVG = {
  // 標靶：三圈同心圓加靶心
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>',
  // 旗子：旗桿加旗面
  flag: '<path d="M6 21V4"/><path d="M6 5h11l-2.5 3.5L17 12H6"/>'
};

function raceIcon(kind) {
  var span = h('span', 'race-icon');
  span.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" '
    + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + RACE_ICON_SVG[kind] + '</svg>';
  return span;
}

// 賽事距離分級（公里）：全馬 42K 與 42.195K 都算全馬，超過 42.5K 才是超馬
var RACE_TIER_ULTRA_KM = 42.5;
var RACE_TIER_FULL_KM = 42;
var RACE_TIER_HALF_KM = 21;
var RACE_TIER_TEN_KM = 10;

/** 距離 → 顏色級別：ultra 紫、full 紅、half 橘、ten 綠（10K～半馬以下）、short 藍（10K 以下）。 */
function raceDistanceTier(km) {
  if (km > RACE_TIER_ULTRA_KM) return 'ultra';
  if (km >= RACE_TIER_FULL_KM) return 'full';
  if (km >= RACE_TIER_HALF_KM) return 'half';
  if (km >= RACE_TIER_TEN_KM) return 'ten';
  return 'short';
}

/** 剩餘訓練週數（無條件進位）；7 天內就是賽前最後一週。 */
function raceWeeksText(daysLeft) {
  var weeks = Math.ceil(daysLeft / 7);
  return daysLeft <= 7 ? '賽前' + weeks + '週' : '剩餘' + weeks + '週';
}

function raceCard(iconKind, label, race, extraClass) {
  var card = h('div', 'race-card' + (extraClass ? ' ' + extraClass : ''));
  var labelRow = h('div', 'race-card-label');
  labelRow.appendChild(raceIcon(iconKind));
  labelRow.appendChild(h('span', null, label));
  card.appendChild(labelRow);
  if (!race) {
    card.appendChild(h('div', 'today-note', '尚未設定'));
    return card;
  }
  var days = h('div', 'race-card-days');
  days.appendChild(h('span', 'race-days', race.days_left === 0 ? '比賽日' : String(race.days_left)));
  if (race.days_left !== 0) {
    days.appendChild(h('span', 'race-days-unit', '天'));
    days.appendChild(h('span', 'race-weeks', '/ ' + raceWeeksText(race.days_left)));
  }
  card.appendChild(days);
  card.appendChild(raceNameRow(race, 'race-card-name'));
  card.appendChild(h('div', 'today-note', race.date));
  return card;
}

/** 賽事名稱＋距離標籤。距離接在名稱後面、字級相同，只靠顏色分級。 */
function raceNameRow(race, className) {
  var nameRow = h('div', className, race.name);
  if (race.distance) {
    nameRow.appendChild(document.createTextNode(' '));
    nameRow.appendChild(h('span', 'race-distance race-dist-' + raceDistanceTier(race.distance_km), race.distance));
  }
  return nameRow;
}

/** 「全部賽事」清單：所有未過期賽事依日期排列，目標賽事以中性的「目標」標記。 */
function raceListPanel(all) {
  var list = h('div', 'race-list');
  all.forEach(function (race) {
    var row = h('div', 'race-list-row' + (race.is_target ? ' is-target' : ''));
    row.appendChild(h('span', 'race-list-date', race.date));
    var name = raceNameRow(race, 'race-list-name');
    if (race.is_target) name.appendChild(h('span', 'race-target-tag', '目標'));
    row.appendChild(name);
    var days = h('span', 'race-list-days');
    days.appendChild(h('span', null, race.days_left === 0 ? '比賽日' : race.days_left + ' 天'));
    if (race.days_left !== 0) days.appendChild(h('span', 'race-weeks', raceWeeksText(race.days_left)));
    row.appendChild(days);
    list.appendChild(row);
  });
  return list;
}

/**
 * 下場賽事與目標賽事各一張卡片，各自記錄剩餘天數。
 * 下場賽事剛好就是目標賽事時合併成一張（同一個天數不必顯示兩次），
 * 標題並列兩個身分並加上目標標記。
 */
export function renderRaceCountdownBlock(races) {
  var block = h('div', 'today-block');
  var title = h('div', 'today-block-title race-title');
  title.appendChild(h('span', null, '賽事倒數'));
  block.appendChild(title);
  var grid = h('div', 'race-grid');
  var next = races && races.next;
  var target = races && races.target;
  if (next && next.is_target) {
    grid.classList.add('is-merged');
    grid.appendChild(raceCard('target', '下場賽事＝目標賽事', next, 'is-target'));
  } else {
    grid.appendChild(raceCard('flag', '下場賽事', next));
    grid.appendChild(raceCard('target', '目標賽事', target, 'is-target'));
  }
  block.appendChild(grid);

  // 卡片已顯示的場次（合併時 1 場、否則最多 2 場）以外還有別場，才需要「全部賽事」
  var all = (races && races.all) || [];
  var shownCount = grid.classList.contains('is-merged') ? 1 : (next ? 1 : 0) + (target ? 1 : 0);
  if (all.length > shownCount) {
    var panel = raceListPanel(all);
    panel.hidden = true;
    var toggle = h('button', 'race-all-btn', '全部賽事（' + all.length + '）');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      toggle.setAttribute('aria-expanded', String(!panel.hidden));
      toggle.classList.toggle('is-open', !panel.hidden);
    });
    title.appendChild(toggle);
    block.appendChild(panel);
  }
  return block;
}

/** 首頁頂端的資料新鮮度。 */
function renderFreshness(data) {
  var node = $('home-freshness');
  if (!node) return;
  clear(node);
  if (!data.freshness) return;
  var f = data.freshness;
  var days = f.days_behind;
  // days_behind 是拿 wellness_latest 與 activity_latest 兩者中較新的
  // 日期去算的（見後端 get_today() 的 most_recent），故顯示文字必須
  // 分開列出兩者，不能只挑其中一個日期配這個天數——挑錯就會出現
  // 「顯示活動日 09-04 卻標『今天』」這種自相矛盾的畫面（曾經發生過）。
  var parts = [];
  if (f.activity_latest) parts.push('活動 ' + f.activity_latest);
  if (f.wellness_latest) parts.push('身體數據 ' + f.wellness_latest);
  node.textContent = parts.length > 0
    ? '資料最後同步：' + parts.join('，')
      + (days === null || days === undefined ? '' : (days > 0 ? '（' + days + ' 天前）' : '（今天）'))
    : '尚無同步資料';
}

export function renderToday() {
  var node = $('today-body');
  clear(node);
  var data = state.today;
  if (!data) { showLoading(node, '載入今日狀態'); return; }

  renderFreshness(data);
  renderSidebarSync();

  var planBlock = h('div', 'today-block');
  var planned = data.planned || {};
  planBlock.appendChild(planBlockTitle(planned));
  planBlock.appendChild(renderTodayPlanRow('今日', planned.today));
  planBlock.appendChild(renderTodayPlanRow('明日', planned.tomorrow));
  node.appendChild(planBlock);

  // 訓練完備程度與本週訓練狀況並排（區塊太窄時上下排）
  var pair = h('div', 'today-pair');
  pair.appendChild(renderTodayReadinessBlock(data.readiness));
  var weekTraining = h('div', 'today-block fig-block');
  weekTraining.id = 'week-training';
  pair.appendChild(weekTraining);
  node.appendChild(pair);
  renderWeekTraining();

  // 賽事倒數放在綜覽最下面
  node.appendChild(renderRaceCountdownBlock(data.races));
}

// ------------------------------------------------------------ 訓練負荷卡片

var LOAD_TIPS = {
  tsb: [
    'TSB（訓練壓力平衡）＝ CTL − ATL：長期體能基礎減去近期疲勞。負值表示近期負荷高於長期基礎。',
    '負荷採 Banister TRIMP：分鐘 × 心率儲備百分比（%HRR）× 隨強度指數上升的權重，'
      + '一小時輕鬆跑約 85、一小時閾值跑約 165。另外標出 TSB 佔 CTL 的百分比，讓自訂尺度的數字可讀。',
    '參考值（Joe Friel，TrainingPeaks〈Applying the Numbers Part 3〉，原為 TSS 尺度）：'
      + '比賽日 +15～+25 通常表現最好；−10～+10 是過渡區；負值代表處於疲勞。'
      + 'TRIMP 與 TSS 定義不同（同一小時閾值跑約為 TSS 的 1.6 倍），絕對值不能直接套用；'
      + 'intervals.icu 改套在「TSB 佔 CTL 的百分比」，社群常引用 −30% 以下為高風險區——這是社群慣例，不是研究定論。本頁不據此判讀。'
  ],
  atl: [
    'ATL（急性訓練負荷）：每日訓練負荷（TRIMP）的 7 日指數加權平均，代表近期疲勞。',
    '指數加權：越近的日子權重越高，一週前的訓練影響約剩三分之一。'
  ],
  ctl: [
    'CTL（慢性訓練負荷）：每日訓練負荷（TRIMP）的 42 日指數加權平均，代表長期累積的體能基礎。',
    '變化很慢：穩定訓練數週才會明顯上升，停練也要數週才明顯下降。'
  ]
};

// 訓練負荷區接在 TSB／ATL／CTL 後面的 Garmin 卡片（views/garmin.js）
var HOME_LOAD_GARMIN_KEYS = ['training_load', 'training_status'];

function lastWith(rows, key) {
  for (var i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i][key] && typeof rows[i][key].value === 'number') return rows[i];
  }
  return null;
}

export function renderHomeLoad() {
  var node = $('home-load');
  if (!node) return;
  clear(node);
  var load = state.today && state.today.load;
  var kpi = state.todayKpi;
  if (!state.today || !kpi) { showLoading(node, '載入訓練負荷'); return; }
  if (!load) { showUnavailable(node, '今日尚無訓練負荷資料'); return; }
  if (load.as_of) node.appendChild(h('div', 'sync-fallback-note', unsyncedNote(load.as_of)));

  var rows = kpi.loadSeries || [];
  var cards = h('div', 'home-cards home-load-cards');
  var tsb = load.tsb && load.tsb.value;
  var ctl = load.ctl && load.ctl.value;
  cards.appendChild(kpiCard({
    label: '狀態平衡',
    abbr: 'TSB',
    value: typeof tsb === 'number' ? (tsb > 0 ? '+' : '') + C.formatNumber(tsb, 1) : '—',
    badge: confBadge(load.tsb),
    note: ctl ? '佔 CTL ' + (tsb > 0 ? '+' : '−') + Math.abs(tsb / ctl * 100).toFixed(0) + '%，近 30 天走勢' : '近 30 天走勢',
    series: rows.map(function (r) { return r.tsb ? r.tsb.value : null; }),
    tip: LOAD_TIPS.tsb
  }));
  cards.appendChild(kpiCard({
    label: '近期疲勞',
    abbr: 'ATL',
    value: load.atl ? C.formatNumber(load.atl.value, 1) : '—',
    badge: confBadge(load.atl),
    note: '7 日指數加權，近 30 天走勢',
    series: rows.map(function (r) { return r.atl ? r.atl.value : null; }),
    tip: LOAD_TIPS.atl
  }));
  cards.appendChild(kpiCard({
    label: '長期體能',
    abbr: 'CTL',
    value: load.ctl ? C.formatNumber(load.ctl.value, 1) : '—',
    badge: confBadge(load.ctl),
    note: '42 日指數加權，近 30 天走勢',
    series: rows.map(function (r) { return r.ctl ? r.ctl.value : null; }),
    tip: LOAD_TIPS.ctl
  }));

  // Garmin 的訓練負荷與訓練狀態（2026-10-22 Fu 指定）：與趨勢頁「Garmin 數據」同一組卡片，
  // 取代原本把兩者擠在一張「Garmin 近期負荷」卡底部小字的寫法。
  var garmin = (kpi.garminCards && kpi.garminCards.cards) || {};
  HOME_LOAD_GARMIN_KEYS.forEach(function (key) {
    if (!garmin[key]) return;
    var slot = h('div');
    renderGarminCard(key, garmin[key], slot);
    cards.appendChild(slot);
  });
  node.appendChild(cards);
}

// ------------------------------------------------------------ 體能狀況卡片

// 首頁體能狀況要放的 Garmin 卡片（2026-10-22 Fu 指定）：原有四張＋身體能量指數＋最大攝氧量。
// 卡片本身與趨勢頁「Garmin 數據」共用 views/garmin.js，這裡只決定放哪幾張。
var HOME_GARMIN_KEYS = ['hrv', 'heart_rate', 'sleep', 'training_readiness', 'body_battery', 'vo2max'];

export function renderHomeBody() {
  var node = $('kpi-strip');
  if (!node) return;
  renderGarminWall(node, state.todayKpi && state.todayKpi.garminCards, HOME_GARMIN_KEYS, '載入體能狀況');
}

/**
 * 首頁的身體狀況與訓練負荷趨勢都取固定的近 30 天（不吃頂列 range），
 * 散在兩支既有端點，這裡併發取回。任一支失敗就當那幾張卡沒資料。
 */
export function loadTodayKpi() {
  Promise.all([
    api('/api/training-load?range=30d').catch(function () { return null; }),
    api('/api/garmin-cards?range=30d').catch(function () { return { cards: {} }; })
  ]).then(function (res) {
    var load = res[0];
    state.todayKpi = {
      loadSeries: (load && load.series) || [],
      garminCards: res[1]
    };
    renderHomeLoad();
    renderHomeBody();
  });
}

// ------------------------------------------------------------ 本課表週

export function renderWeekPlanList() {
  var node = $('week-plan-list');
  if (!node) return;
  clear(node);
  if (!state.weekDays) return;
  var title = $('h-week');
  if (title) title.textContent = '當週課表　' + planWeekLabel(state.weekDays);
  var block = renderPlanWeek(state.weekDays, { selectedDate: todayISO() });
  var zone2 = zone2Tile();
  var tiles = block.querySelector('.pw-tiles');
  if (zone2 && tiles) tiles.appendChild(zone2);
  node.appendChild(block);
}

/**
 * 摘要追加「Zone 2」圖卡：本週（週一到週日）跑步在 Z2 的時間與佔比，來自 /api/overview
 * 的 this_week（與課表週同一個週一起算的範圍）。區間資料還沒回來或週不一致時不顯示。
 * 比例條拆成低強度（Z1–Z2）／中強度（Z3）／高強度（Z4–Z5）三段，並在下方用文字與色點
 * 寫明每段的佔比；黑線標在 80% 處（80/20 的參考位置，不是判讀）。
 */
function zone2Tile() {
  var ov = state.homeOverview;
  var w = ov && ov.available && ov.this_week;
  if (!w || !state.weekDays || !state.weekDays.length || w.week_start !== state.weekDays[0].date) return null;
  var secs = {};
  w.zone_seconds.forEach(function (z) { secs[z.zone] = z.seconds; });
  var total = w.zone_seconds.reduce(function (sum, z) { return sum + z.seconds; }, 0);
  if (total <= 0) return null;
  function pctOf(zones) {
    return zones.reduce(function (sum, z) { return sum + (secs[z] || 0); }, 0) / total * 100;
  }

  var tile = planWeekTile('Zone 2 輕鬆跑');
  tile.title = '本週跑步在 Z2 的停留時間，百分比的分母是本週 Z1～Z5 的總時間';
  tile.appendChild(planWeekTileValue(C.formatDuration(secs[2] || 0),
    '占本週跑步 ' + C.formatNumber(pctOf([2]), 0) + '%'));
  tile.appendChild(zoneSplitBar(w.zone_seconds.map(function (z) {
    return { zone: z.zone, pct: z.seconds / total * 100 };
  }), true));
  // 圖例：每段一列，左邊色點＋名稱，右邊該段的時間與佔比
  var legend = h('div', 'pw-legend');
  [
    { cls: 'bar-intensity-low', text: '低強度 Z1–Z2', zones: [1, 2] },
    { cls: 'bar-intensity-mid', text: '中強度 Z3', zones: [3] },
    { cls: 'bar-intensity-high', text: '高強度 Z4–Z5', zones: [4, 5] }
  ].forEach(function (item) {
    var secsOf = item.zones.reduce(function (sum, z) { return sum + (secs[z] || 0); }, 0);
    var entry = h('div', 'pw-legend-row');
    var name = h('span', 'pw-legend-item');
    name.appendChild(h('span', 'pw-legend-dot ' + item.cls));
    name.appendChild(document.createTextNode(item.text));
    entry.appendChild(name);
    entry.appendChild(h('span', 'pw-legend-val',
      C.formatDuration(secsOf) + '　' + C.formatNumber(pctOf(item.zones), 0) + '%'));
    legend.appendChild(entry);
  });
  legend.appendChild(h('div', 'pw-legend-note', '黑線＝80% 參考位置（80/20 原則），不是判讀'));
  tile.appendChild(legend);
  return tile;
}

export function renderWeekStrip() {
  var node = $('week-strip');
  clear(node);
  if (!state.weekDays) { showLoading(node, '載入本週'); return; }

  var grid = h('div', 'week-strip');
  state.weekDays.forEach(function (day) {
    var cell = h('div', 'week-cell day-' + day.day_state);
    var runnable = firstRunningActivity(day.activities);
    if (runnable) {
      cell.classList.add('is-clickable');
      cell.addEventListener('click', function () {
        navigate('sessions', runnable.id);
      });
    }
    cell.appendChild(h('span', 'week-cell-dow', dowLabel(day.date)));
    cell.appendChild(h('span', 'week-cell-date', day.date.slice(8, 10)));
    if (day.planned.length > 0) {
      var wt = day.planned[0].workout_type;
      cell.appendChild(h('span', 'week-cell-workout', WORKOUT_TYPE_LABEL[wt] || wt));
    }
    // 當週課表只看跑步；健走等其他活動不在這裡標點
    var runs = day.activities.filter(function (act) { return act.is_running; });
    if (runs.length > 0) cell.appendChild(activityDots(runs));
    cell.title = day.date + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
    grid.appendChild(cell);
  });
  node.appendChild(grid);
}

/** 本課表週（週一到週日，見 plan-week.js）：週條、執行表與本週訓練卡共用同一份資料。 */
export function loadWeekStrip() {
  loadPlanWeek(todayISO()).then(function (days) {
    state.weekDays = days;
    renderWeekStrip();
    renderWeekPlanList();
    renderWeekTraining();
  }).catch(function (err) {
    showError($('week-strip'), err.message);
    if ($('week-training')) showError($('week-training'), err.message);
  });
}

// ------------------------------------------------------------ 本週訓練

// 環形進度圖的半徑（viewBox 單位）；圓周用來換算 stroke-dasharray
var RING_R = 26;
var RING_CIRCUMFERENCE = 2 * Math.PI * RING_R;

/** 完成率分級（%）→ 環的顏色：≥90 藍、≥50 綠、≥20 黃、其餘（<20）紅。 */
var PROGRESS_TIERS = [
  { min: 90, cls: 'pct-blue' },
  { min: 50, cls: 'pct-green' },
  { min: 20, cls: 'pct-yellow' },
  { min: -Infinity, cls: 'pct-red' }
];

function progressTierClass(pct) {
  for (var i = 0; i < PROGRESS_TIERS.length; i += 1) {
    if (pct >= PROGRESS_TIERS[i].min) return PROGRESS_TIERS[i].cls;
  }
  return 'pct-red';
}

/** 完成率環形圖：中間顯示百分比、環依完成率分級上色。超過 100% 時環畫滿，數字照實顯示。 */
function progressRing(pct) {
  var wrap = h('div', 'wt-ring ' + progressTierClass(pct));
  var filled = Math.max(0, Math.min(100, pct)) / 100 * RING_CIRCUMFERENCE;
  wrap.innerHTML = '<svg viewBox="3 3 58 58" aria-hidden="true">'
    + '<circle class="wt-ring-track" cx="32" cy="32" r="' + RING_R + '"/>'
    + '<circle class="wt-ring-fill" cx="32" cy="32" r="' + RING_R + '" '
    + 'stroke-dasharray="' + filled.toFixed(1) + ' ' + RING_CIRCUMFERENCE.toFixed(1) + '" '
    + 'transform="rotate(-90 32 32)"/></svg>';
  wrap.appendChild(h('span', 'wt-ring-pct', Math.round(pct) + '%'));
  return wrap;
}

/** 一列「實際 / 課表」：大數字＋淡化的分母；planned 為 null 時只顯示實際與單位。 */
function weekFigRow(label, done, planned, unit) {
  var row = h('div', 'wt-row');
  row.appendChild(h('span', 'wt-row-label', label));
  var val = h('span', 'wt-row-value');
  val.appendChild(h('span', 'wt-done', done));
  val.appendChild(h('span', 'wt-planned',
    planned === null ? unit : '/ ' + planned + (unit ? ' ' + unit : '')));
  row.appendChild(val);
  return row;
}

/** 秒 → 小時（小數一位），本週訓練狀況的時間一律用小時呈現。 */
function hoursText(sec) {
  return C.formatNumber(sec / 3600, 1);
}

/**
 * 本週訓練狀況：本課表週（週一到週日）實際跑量對照課表、實際累計時間，環形圖顯示里程完成率。
 * 時間只列實際，不對照預計時間（分母不可靠，見 plan-week.js planWeekSummary）。
 *
 * 放在「今天」區塊內、訓練完備程度旁（#week-training 由 renderToday 建立）。
 * 課表週與今日資料各自載入、先後不定，兩邊資料回來時都會呼叫本函式；
 * 容器還不存在（今日資料未到）時直接略過，等 renderToday 建好再畫。
 */
export function renderWeekTraining() {
  var node = $('week-training');
  if (!node) return;
  clear(node);
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '本週訓練狀況'));
  if (state.weekDays && state.weekDays.length) {
    var first = state.weekDays[0].date;
    var last = state.weekDays[state.weekDays.length - 1].date;
    title.appendChild(h('span', 'today-note fig-title-date',
      C.shortDate(first) + ' (' + dowLabel(first) + ') ~ ' + C.shortDate(last) + ' (' + dowLabel(last) + ')'));
  }
  node.appendChild(title);

  var body = h('div');
  node.appendChild(body);
  if (!state.weekDays) { showLoading(body, '載入本週訓練狀況'); return; }

  var s = planWeekSummary(state.weekDays);
  if (s.plannedSessions === 0) {
    showUnavailable(body, '本週沒有課表，無法計算完成量');
    return;
  }

  // 里程與時間兩列在左、完成率環形圖在右；說明文字放在下方整列
  var card = h('div', 'wt-card');
  var figs = h('div', 'wt-figs');
  figs.appendChild(weekFigRow('里程',
    C.formatNumber(s.doneKm, 1),
    s.plannedKm > 0 ? C.formatNumber(s.plannedKm, 1) : '—', 'km'));
  figs.appendChild(weekFigRow('時間', hoursText(s.doneSec), null, 'h'));
  card.appendChild(figs);

  if (s.plannedKm > 0) {
    var pct = s.doneKm / s.plannedKm * 100;
    var ring = progressRing(pct);
    ring.title = '以里程計算：' + Math.round(pct) + '%';
    card.appendChild(ring);
  }
  body.appendChild(card);
}

// ------------------------------------------------------------ 戰力綜覽

function vdotCard(vdot) {
  var race = vdot.race || {};
  var ref = vdot.garmin_reference;
  var refText = null;
  if (ref && ref.predictions.length > 0) {
    var vals = ref.predictions.map(function (p) { return p.vdot; });
    var lo = Math.min.apply(null, vals);
    var hi = Math.max.apply(null, vals);
    refText = 'Garmin 對照 ' + C.formatNumber(lo, 1) + (hi !== lo ? '–' + C.formatNumber(hi, 1) : '');
  }
  var tip = [
    'Jack Daniels 的跑力指數：用一場全力成績反推的有氧能力分數。',
    '只採用標記為比賽或全力測驗的成績（3K／5K／10K 需在 90 天內，半馬／全馬 180 天內），'
      + '把實際距離與完賽時間代入 Daniels & Gilbert 公式：VDOT ＝ 該配速的攝氧成本 ÷ 能持續該時間的最大攝氧比例。'
      + '有手動計時成績的場次用手動計時成績。',
    '走勢線是歷年每場比賽各自算出的 VDOT（不受新鮮度門檻限制）。',
    '不採用一般訓練經心率換算的推估：那是次最大強度推估，對訓練量少的跑者會偏高。'
  ];
  if (ref) {
    tip.push('Garmin 對照值：把手錶 ' + ref.as_of + ' 的完賽預測（'
      + ref.predictions.map(function (p) {
        return p.distance + ' ' + C.formatDuration(p.time_sec) + ' → ' + C.formatNumber(p.vdot, 1);
      }).join('、')
      + '）用同一公式換算。Garmin 演算法未公開，僅供對照，不作為依據。');
  }
  var history = (vdot.history || []).map(function (x) { return x.vdot; });
  if (!race.value) {
    return kpiCard({
      label: '跑力', abbr: 'VDOT', value: '未評',
      badge: h('span', 'conf-badge conf-none', '無資料'),
      note: race.reason + (refText ? '；' + refText : ''),
      series: history.length > 1 ? history : null,
      tip: tip
    });
  }
  return kpiCard({
    label: '跑力',
    abbr: 'VDOT',
    value: C.formatNumber(race.value.value, 1),
    badge: confBadge(race.value),
    note: '依 ' + C.shortDate(race.basis.date) + ' ' + race.basis.distance + (refText ? '，' + refText : ''),
    series: history.length > 1 ? history : null,
    tip: tip
  });
}

// 展開中的距離（distance 代碼）；重繪時保留，切換頁面再回來也還在
var pbOpenDistance = null;

/** 卡片上的賽事名稱：個人測驗一律顯示「個人測驗」，完整名稱留給展開清單 */
function pbCardName(r) {
  return r.personal_test ? '個人測驗' : (r.name || '');
}

function pbCard(pb) {
  var count = pb.records.length;
  var expandable = count > 0;
  var card = h(expandable ? 'button' : 'div', 'pb-card');
  if (count === 0) card.className += ' is-empty';
  if (expandable) {
    card.type = 'button';
    card.className += ' is-expandable' + (pbOpenDistance === pb.distance ? ' is-open' : '');
    card.setAttribute('aria-expanded', pbOpenDistance === pb.distance ? 'true' : 'false');
    card.setAttribute('aria-controls', 'pb-detail');
    card.addEventListener('click', function () {
      pbOpenDistance = pbOpenDistance === pb.distance ? null : pb.distance;
      renderAbility();
      var target = pbOpenDistance ? $('pb-detail') : null;
      if (target) target.focus({ preventScroll: true });
    });
  }

  var top = h('div', 'pb-card-head');
  top.appendChild(h('span', 'pb-distance', pb.label));
  if (expandable) top.appendChild(h('span', 'pb-count', count > 1 ? '共 ' + count + ' 場' : ''));
  card.appendChild(top);

  if (count === 0) {
    card.appendChild(h('div', 'pb-empty-text', '尚無紀錄'));
    return card;
  }
  var best = pb.records[0];
  card.appendChild(h('div', 'pb-time', C.formatDuration(best.time_sec)));
  card.appendChild(h('div', 'pb-pace', C.formatPace(best.pace_sec_per_km) + '/km'));
  card.appendChild(h('div', 'pb-date', best.date));
  card.appendChild(h('div', 'pb-vdot', 'VDOT ' + C.formatNumber(best.vdot, 1)));
  var name = h('div', 'pb-name', pbCardName(best));
  name.title = best.name || '';
  card.appendChild(name);
  return card;
}

function pbDetail(pb) {
  var panel = h('div', 'pb-detail');
  panel.id = 'pb-detail';
  panel.tabIndex = -1;
  var head = h('div', 'pb-detail-head');
  head.appendChild(h('h3', 'pb-detail-title', pb.label + ' 歷史成績（共 ' + pb.records.length + ' 場'
    + (pb.records.length > 1 ? '，由快到慢）' : '）')));
  var close = h('button', 'pb-detail-close', '✕');
  close.type = 'button';
  close.setAttribute('aria-label', '收合 ' + pb.label + ' 歷史成績');
  close.addEventListener('click', function () {
    pbOpenDistance = null;
    renderAbility();
  });
  head.appendChild(close);
  panel.appendChild(head);

  var list = h('ol', 'pb-list');
  pb.records.forEach(function (r, i) {
    var item = h('li');
    var row = h('button', 'pb-row' + (i === 0 ? ' is-best' : ''));
    row.type = 'button';
    row.setAttribute('aria-label', '第 ' + (i + 1) + ' 名 ' + C.formatDuration(r.time_sec) + '，' + r.date + '，' + (r.name || '') + '，檢視單場分析');
    row.appendChild(h('span', 'pb-row-rank', String(i + 1)));
    var time = h('span', 'pb-row-time', C.formatDuration(r.time_sec));
    if (r.official) {
      var manual = h('span', 'pb-manual', '手動');
      manual.title = '時間為人工填入（大會成績或現場計時），非手錶紀錄';
      time.appendChild(manual);
    }
    row.appendChild(time);
    row.appendChild(h('span', 'pb-row-pace', C.formatPace(r.pace_sec_per_km) + '/km'));
    row.appendChild(h('span', 'pb-row-date', r.date));
    row.appendChild(h('span', 'pb-row-name', r.name || ''));
    row.appendChild(h('span', 'pb-row-vdot', 'VDOT ' + C.formatNumber(r.vdot, 1)));
    row.addEventListener('click', function () { navigate('sessions', r.id); });
    item.appendChild(row);
    list.appendChild(item);
  });
  panel.appendChild(list);
  return panel;
}

/** VDOT 卡＋各距離個人最佳成績卡（點卡片展開該距離的全部成績）。 */
var ABILITY_PB_ORDER = ['half_marathon', 'marathon', '3k', '5k', '10k'];

function abilityPbRank(distance) {
  var i = ABILITY_PB_ORDER.indexOf(distance);
  return i === -1 ? ABILITY_PB_ORDER.length : i;
}

export function renderAbility() {
  var node = $('home-ability');
  if (!node) return;
  closeInfoPopover(false); // 重繪會換掉 ⓘ 按鈕，開著的說明要先關
  clear(node);
  var ov = state.homeOverview;
  if (!ov) { showLoading(node, '載入戰力綜覽'); return; }
  if (!ov.available) { showUnavailable(node, ov.reason); return; }

  var byDistance = {};
  ov.personal_bests.forEach(function (pb) { byDistance[pb.distance] = pb; });
  if (pbOpenDistance && !(byDistance[pbOpenDistance] && byDistance[pbOpenDistance].records.length)) {
    pbOpenDistance = null;
  }

  // 顯示順序：跑力、半馬、全馬；3K、5K、10K（一列三張，長距離在前）。
  // API 回傳的是由短到長，這只是呈現順序；沒列在 ABILITY_PB_ORDER 的距離排在最後
  var grid = h('div', 'ability-grid');
  grid.appendChild(vdotCard(ov.vdot));
  ov.personal_bests.slice().sort(function (a, b) {
    return abilityPbRank(a.distance) - abilityPbRank(b.distance);
  }).forEach(function (pb) { grid.appendChild(pbCard(pb)); });
  node.appendChild(grid);
  node.appendChild(h('p', 'chart-note',
    '個人最佳成績為生涯紀錄：標記為 Race 的場次（含個人計時測驗），每個距離顯示最佳一筆，點卡片看全部成績。'
    + '賽事名稱讀自學員檔 athlete_profile.md，配速一律為「時間 ÷ 標準距離」。'));
  if (pbOpenDistance) node.appendChild(pbDetail(byDistance[pbOpenDistance]));
}

/**
 * 首頁只用 /api/overview 裡不受時間範圍影響的欄位（VDOT、個人最佳成績、本週 Zone 2），
 * 所以固定取一次、與頂列 range 無關（趨勢頁的區間摘要另外用 state.overview）。
 */
export function loadHomeOverview() {
  api('/api/overview?range=30d').then(function (data) {
    state.homeOverview = data;
    renderAbility();
    renderWeekPlanList();
  }).catch(function (err) {
    showError($('home-ability'), err.message);
  });
}

// ------------------------------------------------------------ 最近訓練

export function renderRecentSessions() {
  var node = $('recent-sessions');
  if (!node) return;
  clear(node);
  if (!state.recentSessions) { showLoading(node, '載入近期活動'); return; }
  if (state.recentSessions.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '近 30 天沒有跑步場次'));
    return;
  }

  var list = h('div', 'recent-list');
  state.recentSessions.slice(0, 8).forEach(function (s) {
    var row = h('button', 'recent-row', null);
    row.type = 'button';
    row.addEventListener('click', function () { navigate('sessions', s.id); });

    row.appendChild(h('span', 'rc-date', s.date.slice(5)));
    row.appendChild(h('span', 'rc-type', WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type || '未分類'));
    var plan = state.recentPlans && state.recentPlans[s.id];
    row.appendChild(h('span', 'rc-plan', plan ? planSummary(plan) : '—'));
    row.appendChild(h('span', 'rc-km', s.distance_km ? C.formatNumber(s.distance_km, 2) + ' km' : '—'));
    row.appendChild(h('span', 'rc-pace', s.avg_pace_sec_per_km ? C.formatPace(s.avg_pace_sec_per_km) + '/km' : '—'));
    row.appendChild(h('span', 'rc-hr', s.avg_hr_bpm ? s.avg_hr_bpm + ' bpm' : '—'));
    list.appendChild(row);
  });
  node.appendChild(list);
}

/**
 * 近 30 天的場次，連同各場對應的課表。/api/sessions 不含課表，故另取同一段
 * 期間的 /api/calendar，以活動 id 找到當天課表（plan_match 為 none 的不算對應）。
 * 行事曆抓不到時只是少了課表欄，不影響清單本身。
 */
export function loadRecentSessions() {
  var today = todayISO();
  Promise.all([
    api('/api/sessions?range=30d'),
    api('/api/calendar?start=' + addDaysISO(today, -29) + '&end=' + today).catch(function () { return null; })
  ]).then(function (res) {
    state.recentSessions = (res[0] && res[0].sessions) || [];
    var plans = {};
    ((res[1] && res[1].days) || []).forEach(function (d) {
      var p = (d.planned || [])[0];
      if (!p) return;
      (d.activities || []).forEach(function (a) {
        if (a.plan_match && a.plan_match.state !== 'none') plans[a.id] = p;
      });
    });
    state.recentPlans = plans;
    renderRecentSessions();
  }).catch(function (err) {
    showError($('recent-sessions'), err.message);
  });
}

// ------------------------------------------------------------ 載入

export function loadToday() {
  showLoading($('today-body'), '載入今日狀態');
  api('/api/today').then(function (data) {
    state.today = data;
    renderToday();
    renderHomeLoad();
    renderHomeBody(); // HRV 的事實（含信賴度）與 Garmin 狀態來自 /api/today
    // 設定頁的「最後同步」與側邊列同源，資料回來時一併更新（設定頁此刻
    // 可能不在畫面上，但它沒有圖表、不吃容器寬度，直接寫入即可）
    renderSettingsSync();
  }).catch(function (err) {
    showError($('today-body'), err.message);
  });
}

/** 首頁全部區塊的資料（同步完成後也呼叫這裡重新整理）。 */
export function loadHome() {
  loadToday();
  loadTodayKpi();
  loadWeekStrip();
  loadHomeOverview();
  loadRecentSessions();
}
