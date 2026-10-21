/**
 * home.js — 首頁 view（2026-10-21 由「今日」與「總覽」合併）
 *
 * 固定看「現在」，不吃頂列的時間範圍——會隨範圍變動的走勢都在趨勢頁。
 * 6 個區塊，依「回答什麼問題」分（分類見 docs/dev/FRONTEND_REFACTOR.md 階段 6）：
 *   今天（課表＋準備度）、訓練負荷、身體狀況、本課表週、能力與成績、最近訓練
 * 區塊的順序與顯隱由使用者自訂（core/customize.js，偏好 key 'home'）。
 *
 * 純呈現層：把三欄位事實（數值/來源/信賴度）與課表原樣顯示出來，
 * 不做任何「今天該不該練」的判讀——判讀留給 AI 教練（見 PLAN.md §7.3、
 * mcp/server.py 的 coach 行為約束）。各指標的 ⓘ 只寫定義與算法。
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, DAY_STATE_LABEL, CONFIDENCE_LABEL, HRV_STATUS_LABEL, HRV_STATUS_CLASS, GARMIN_TRAINING_STATUS_LABEL, GARMIN_ACWR_STATUS_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError, showUnavailable } from '../core/dom.js';
import { confBadge, kpiCard, closeInfoPopover } from '../core/facts.js';
import { fmtDelta, addDaysISO, todayISO, dowLabel, planSummary } from '../core/format.js';
import { customizer, layoutOf } from '../core/customize.js';
import { loadPlanWeek, planWeekLabel, renderPlanWeek, planWeekFig } from './plan-week.js';
import { activityDots, firstRunningActivity } from './calendar.js';
import { renderSettingsSync } from './settings.js';
import { zoneSplitBar } from './overview.js';
import { navigate } from '../shell/router.js';
import { renderSidebarSync } from '../shell/sync.js';

// ------------------------------------------------------------ 區塊版面

// half：可與相鄰的另一個半欄區塊並排（見 applyHomeLayout）
var HOME_BLOCKS = [
  { key: 'today', label: '今天', half: true },
  { key: 'load', label: '訓練負荷', half: true },
  { key: 'body', label: '身體狀況' },
  { key: 'week', label: '本課表週' },
  { key: 'ability', label: '能力與成績' },
  { key: 'recent', label: '最近訓練' }
];

/**
 * 依使用者偏好重排區塊（移動既有 DOM，不重畫內容）並套用顯隱。
 * 半欄區塊只有與另一個半欄區塊相鄰時才並排；落單的佔滿整欄，避免半邊空白。
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
  for (var i = 0; i < visible.length - 1; i += 1) {
    if (visible[i].half && visible[i + 1].half) {
      $('block-' + visible[i].key).classList.add('is-half');
      $('block-' + visible[i + 1].key).classList.add('is-half');
      i += 1;
    }
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
    hint: '調整區塊順序，取消勾選可隱藏。「今天」與「訓練負荷」相鄰時並排。',
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

// ------------------------------------------------------------ 今天：課表＋準備度

export function renderTodayPlanRow(label, plan) {
  var row = h('div', 'today-plan-row');
  row.appendChild(h('span', 'today-plan-label', label));
  if (!plan) {
    row.appendChild(h('span', 'today-plan-value', '無課表'));
    return row;
  }
  var text = (WORKOUT_TYPE_LABEL[plan.workout_type] || plan.workout_type || '—');
  if (plan.planned_distance_km !== null && plan.planned_distance_km !== undefined && plan.planned_distance_km > 0) {
    text += '　' + C.formatNumber(plan.planned_distance_km, 1) + ' km';
  }
  if (plan.planned_pace_sec_per_km) {
    text += '　' + C.formatPace(plan.planned_pace_sec_per_km) + '/km';
  }
  row.appendChild(h('span', 'today-plan-value', text));
  if (plan.created_at) {
    row.appendChild(h('span', 'today-note', '課表版本 ' + plan.created_at.slice(0, 10)));
  }
  return row;
}

export function renderTodayReadinessBlock(readiness) {
  var block = h('div', 'today-block');
  block.appendChild(h('div', 'today-block-title', '準備度（規則引擎）'));

  var today = readiness && readiness.today;
  if (!today || today.readiness === null || today.readiness === undefined) {
    var reasons = (today && today.not_assessed || [])
      .map(function (na) { return na.reason; });
    block.appendChild(h('div', 'state-msg state-unavailable',
      reasons.length ? reasons[0] : '今日尚無足夠資料可評估'));
  } else {
    var row = h('div', 'today-fact-row');
    row.appendChild(h('span', 'today-fact-label', '今日判定'));
    row.appendChild(h('span', 'today-fact-value', today.readiness === 'low' ? '低' : '正常'));
    block.appendChild(row);
    (today.triggers || []).forEach(function (t) {
      var trow = h('div', 'today-plan-row');
      trow.appendChild(h('span', 'today-plan-label', ''));
      var detailWrap = h('span', 'today-plan-value', t.detail);
      trow.appendChild(detailWrap);
      var level = (t.confidence && t.confidence.level) || 'high';
      trow.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
      block.appendChild(trow);
    });
  }

  var latest = readiness && readiness.latest_evaluable;
  if (latest && (!today || today.readiness === null || today.readiness === undefined)) {
    block.appendChild(h('div', 'today-note',
      '最近一次可評估：' + latest.date + '　' + (latest.readiness === 'low' ? '低' : '正常')));
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
  planBlock.appendChild(h('div', 'today-block-title', '課表'));
  var planned = data.planned || {};
  planBlock.appendChild(renderTodayPlanRow('今日', planned.today));
  planBlock.appendChild(renderTodayPlanRow('明日', planned.tomorrow));
  node.appendChild(planBlock);
  node.appendChild(renderTodayReadinessBlock(data.readiness));
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
  ],
  garmin: [
    'Garmin（Firstbeat）以 EPOC（運動後過量耗氧）推估的 7 日急性負荷，演算法未公開，尺度與本專案的 TRIMP 不同。',
    '建議範圍與負荷比都是手錶的輸出，僅供對照方向，不作為判讀依據。'
  ]
};

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

  var rows = kpi.loadSeries || [];
  var cards = h('div', 'home-cards');
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

  var ref = kpi.garminLoad;
  var latest = ref && ref.series && ref.series.length ? ref.series[ref.series.length - 1] : null;
  if (latest) {
    var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
    refBadge.title = ref.note || '';
    var statusKey = (latest.status || '').replace(/_\d+$/, '');
    var parts = [];
    if (latest.acute_load_optimal_min !== null && latest.acute_load_optimal_max !== null) {
      parts.push('建議 ' + C.formatNumber(latest.acute_load_optimal_min, 0) + '–' + C.formatNumber(latest.acute_load_optimal_max, 0));
    }
    if (latest.acwr !== null) {
      parts.push('負荷比 ' + C.formatNumber(latest.acwr, 1)
        + (latest.acwr_status ? '（' + (GARMIN_ACWR_STATUS_LABEL[latest.acwr_status] || latest.acwr_status) + '）' : ''));
    }
    if (statusKey) parts.push(GARMIN_TRAINING_STATUS_LABEL[statusKey] || latest.status);
    cards.appendChild(kpiCard({
      label: 'Garmin 近期負荷',
      value: C.formatNumber(latest.acute_load, 0),
      badge: refBadge,
      note: parts.join('，'),
      series: ref.series.map(function (d) { return d.acute_load; }),
      tip: LOAD_TIPS.garmin
    }));
  }
  node.appendChild(cards);
}

// ------------------------------------------------------------ 身體狀況卡片

function numericValues(points) {
  return (points || []).map(function (p) { return p.value; })
    .filter(function (v) { return typeof v === 'number' && isFinite(v); });
}

function average(vals) {
  return vals.length ? vals.reduce(function (a, v) { return a + v; }, 0) / vals.length : null;
}

/** HRV 卡下方的補充：7 日均值、昨夜較 7 日均值、Garmin 的 HRV 狀態（僅供對照）。 */
function hrvDetails(hrv) {
  var wrap = h('div', 'kpi-detail');
  if (hrv.weekly_avg_ms) {
    var weekly = h('div', 'kpi-detail-row');
    weekly.appendChild(h('span', null, '7 日均值 ' + C.formatNumber(hrv.weekly_avg_ms.value, 1) + ' ms'));
    if (hrv.delta_pct && typeof hrv.delta_pct.value === 'number') {
      weekly.appendChild(h('span', null, '，昨夜較 7 日 ' + fmtDelta(hrv.delta_pct.value, 1, '%')));
      weekly.appendChild(confBadge(hrv.delta_pct));
    }
    wrap.appendChild(weekly);
  }
  // Garmin 自己的 HRV 狀態走 garmin_reference（for_display_only），不是 fact() 的
  // 三欄位契約——規則引擎與 AI 教練都不讀它，這裡純粹是「讓你看到手錶顯示了什麼」。
  // 平衡區間對照的是 7 日均值、不是昨夜值（實測 39/39 天吻合，昨夜值只有 29/39）。
  var gref = hrv.garmin_reference;
  if (gref && gref.status) {
    var status = h('div', 'kpi-detail-row');
    status.appendChild(h('span', null, 'Garmin 狀態 '));
    status.appendChild(h('span', HRV_STATUS_CLASS[gref.status] || '', HRV_STATUS_LABEL[gref.status] || gref.status));
    if (gref.baseline_balanced_low && gref.baseline_balanced_upper) {
      status.appendChild(h('span', null, '（平衡區間 ' + C.formatNumber(gref.baseline_balanced_low, 0) + '–'
        + C.formatNumber(gref.baseline_balanced_upper, 0) + ' ms）'));
    }
    var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
    refBadge.title = gref.note || '';
    status.appendChild(refBadge);
    wrap.appendChild(status);
  }
  return wrap;
}

export function renderHomeBody() {
  var node = $('kpi-strip');
  if (!node) return;
  clear(node);
  var kpi = state.todayKpi;
  if (!kpi) { showLoading(node, '載入身體狀況'); return; }

  var cards = h('div', 'home-cards');
  var hrv = state.today && state.today.hrv;

  // HRV：主數字取 /api/today 的事實（帶信賴度）；沒有時退回 30 天序列的最後一筆
  var hrvVals = numericValues(kpi.hrvPoints);
  var hrvToday = hrv && hrv.today_ms && typeof hrv.today_ms.value === 'number' ? hrv.today_ms : null;
  if (hrvToday || hrvVals.length) {
    var hrvCard = kpiCard({
      label: '心率變異度',
      abbr: 'HRV 昨夜',
      value: C.formatNumber(hrvToday ? hrvToday.value : hrvVals[hrvVals.length - 1], hrvToday ? 1 : 0),
      unit: 'ms',
      badge: hrvToday ? confBadge(hrvToday) : null,
      note: hrv && hrv.as_of ? hrv.as_of + ' 的資料（今日尚未同步）' : '近 30 天走勢',
      series: hrvVals,
      tip: [
        '心率變異度（HRV）：睡眠期間相鄰心跳間隔變化的均方根（RMSSD），由手錶整夜量測後取平均。',
        '數值高低因人而異，只能和自己的基準比較；單晚的值波動大，看 7 日均值與走勢比較穩。',
        'Garmin 的 HRV 狀態與平衡區間是拿 7 日均值判的，不是昨夜值。'
      ]
    });
    if (hrv) hrvCard.appendChild(hrvDetails(hrv));
    cards.appendChild(hrvCard);
  }

  var rhrVals = numericValues(kpi.rhrPoints);
  if (rhrVals.length) {
    cards.appendChild(kpiCard({
      label: '靜止心率',
      value: C.formatNumber(rhrVals[rhrVals.length - 1], 0),
      unit: 'bpm',
      note: '30 天平均 ' + C.formatNumber(average(rhrVals), 0) + ' bpm',
      series: rhrVals,
      invert: true,
      tip: ['手錶整天量到的最低心率區段平均（多在睡眠中）。', '和自己近期的平均比較才有意義；連續幾天明顯偏高，常與疲勞、睡眠不足或生病有關。']
    }));
  }

  var sleepVals = numericValues(kpi.sleepPoints);
  if (sleepVals.length) {
    var durs = numericValues(kpi.sleepDurationPoints);
    var lastDur = durs.length ? durs[durs.length - 1] : null;
    cards.appendChild(kpiCard({
      label: '睡眠分數',
      value: C.formatNumber(sleepVals[sleepVals.length - 1], 0),
      note: lastDur ? '時長 ' + Math.floor(lastDur / 3600) + ' 小時 ' + Math.round((lastDur % 3600) / 60) + ' 分' : '近 30 天走勢',
      series: sleepVals,
      tip: ['Garmin 依睡眠時長、各睡眠階段比例、壓力與中斷次數算出的 0–100 分數，演算法未公開。']
    }));
  }

  var readyVals = numericValues(kpi.readinessPoints);
  if (readyVals.length) {
    var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
    cards.appendChild(kpiCard({
      label: 'Garmin 準備度',
      value: C.formatNumber(readyVals[readyVals.length - 1], 0),
      badge: refBadge,
      note: '近 30 天走勢',
      series: readyVals,
      tip: ['Garmin 綜合睡眠、恢復時間、HRV 與近期負荷給出的 0–100 分數，演算法未公開。',
        '與「今天」區塊的準備度（本專案規則引擎）是兩回事：規則引擎的依據公開、帶信賴度；這個只供對照。']
    }));
  }

  if (!cards.childNodes.length) {
    showUnavailable(node, '近 30 天沒有身體數據');
    return;
  }
  node.appendChild(cards);
}

/**
 * 首頁的身體狀況與訓練負荷趨勢都取固定的近 30 天（不吃頂列 range），
 * 散在兩支既有端點，這裡併發取回。任一支失敗就當那幾張卡沒資料。
 */
export function loadTodayKpi() {
  Promise.all([
    api('/api/training-load?range=30d').catch(function () { return null; }),
    api('/api/wellness-trend?range=30d').catch(function () { return null; })
  ]).then(function (res) {
    var load = res[0];
    var wellness = res[1];
    var metrics = (wellness && wellness.metrics) || {};
    function pts(key) { return (metrics[key] && metrics[key].points) || []; }
    state.todayKpi = {
      loadSeries: (load && load.series) || [],
      garminLoad: (load && load.garmin_reference) || null,
      hrvPoints: pts('hrv_ms'),
      rhrPoints: pts('resting_hr_bpm'),
      sleepPoints: pts('sleep_score'),
      sleepDurationPoints: pts('sleep_duration_sec'),
      readinessPoints: pts('training_readiness_score')
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
  if (title) title.textContent = '本課表週　' + planWeekLabel(state.weekDays);
  var block = renderPlanWeek(state.weekDays, { selectedDate: todayISO() });
  var zone2 = zone2Fig();
  var figs = block.querySelector('.pw-figs');
  if (zone2 && figs) figs.appendChild(zone2);
  node.appendChild(block);
}

/**
 * 摘要列追加「Zone 2」：本週（週一到週日）跑步在 Z2 的時間與佔比，來自 /api/overview
 * 的 this_week（與課表週同一個週一起算的範圍）。區間資料還沒回來或週不一致時不顯示。
 */
function zone2Fig() {
  var ov = state.homeOverview;
  var w = ov && ov.available && ov.this_week;
  if (!w || !state.weekDays || !state.weekDays.length || w.week_start !== state.weekDays[0].date) return null;
  var secs = {};
  w.zone_seconds.forEach(function (z) { secs[z.zone] = z.seconds; });
  var total = w.zone_seconds.reduce(function (s, z) { return s + z.seconds; }, 0);
  if (total <= 0) return null;
  var fig = planWeekFig('Zone 2', C.formatDuration(secs[2] || 0),
    '（' + C.formatNumber((secs[2] || 0) / total * 100, 0) + '%）');
  fig.title = '本週跑步在 Z2 輕鬆的停留時間，百分比的分母是本週 Z1～Z5 的總時間';
  fig.appendChild(zoneSplitBar(w.zone_seconds.map(function (z) {
    return { zone: z.zone, pct: z.seconds / total * 100 };
  }), true));
  return fig;
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
    if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
    cell.title = day.date + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
    grid.appendChild(cell);
  });
  node.appendChild(grid);
}

/** 本課表週（週一到週日，見 plan-week.js）：週條與執行表共用同一份資料。 */
export function loadWeekStrip() {
  loadPlanWeek(todayISO()).then(function (days) {
    state.weekDays = days;
    renderWeekStrip();
    renderWeekPlanList();
  }).catch(function (err) {
    showError($('week-strip'), err.message);
  });
}

// ------------------------------------------------------------ 能力與成績

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
export function renderAbility() {
  var node = $('home-ability');
  if (!node) return;
  closeInfoPopover(false); // 重繪會換掉 ⓘ 按鈕，開著的說明要先關
  clear(node);
  var ov = state.homeOverview;
  if (!ov) { showLoading(node, '載入能力與成績'); return; }
  if (!ov.available) { showUnavailable(node, ov.reason); return; }

  var byDistance = {};
  ov.personal_bests.forEach(function (pb) { byDistance[pb.distance] = pb; });
  if (pbOpenDistance && !(byDistance[pbOpenDistance] && byDistance[pbOpenDistance].records.length)) {
    pbOpenDistance = null;
  }

  var grid = h('div', 'ability-grid');
  grid.appendChild(vdotCard(ov.vdot));
  ov.personal_bests.forEach(function (pb) { grid.appendChild(pbCard(pb)); });
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
  if (!state.recentSessions) { showLoading(node, '載入最近訓練'); return; }
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
