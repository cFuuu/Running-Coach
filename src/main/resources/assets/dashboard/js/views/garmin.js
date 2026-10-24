/**
 * garmin.js — Garmin 網頁式卡片牆（/api/garmin-cards）
 *
 * 趨勢頁「Garmin 數據」類別與首頁「體能狀況」共用同一組卡片：GARMIN_CARD_DEFS
 * 是唯一的卡片定義，每張卡的 render(card) 吃 API 回傳裡對應的那一塊。
 * 版面仿 Garmin Connect 網頁（2026-10-22 Fu 提供的截圖）：卡片標題、主數字、
 * 環形／分段量表或日內小圖、底部說明。
 *
 * 這裡的數字全是 Garmin 的輸出，只供顯示；本專案的判斷（綜合整備狀態等）
 * 不讀這支 API。唯二由本專案補上的是 VO2max 分級（API 不提供，查 Garmin 手冊
 * 的公開表）與熱血時間週累計，ⓘ 說明裡都寫明。
 */

import * as C from '../charts.js';
import { h, clear, showLoading, showUnavailable } from '../core/dom.js';
import { todayISO } from '../core/format.js';
import { infoTip, hrvStatusMark, toneMark } from '../core/facts.js';
import { HRV_STATUS_LABEL, GARMIN_TRAINING_STATUS_LABEL, GARMIN_ACWR_STATUS_LABEL } from '../core/state.js';

// Training Readiness 等級。MODERATE「中」對照 Garmin 網頁；其餘依 Garmin 的五級
// （差／低／中／高／最佳）。分數區間是 Garmin 公布的：1–24／25–49／50–74／75–94／95–100。
var READINESS_LEVEL_LABEL = { POOR: '差', LOW: '低', MODERATE: '中', HIGH: '高', PRIME: '最佳' };
var READINESS_BANDS = [
  { to: 25, className: 'gc-tone-red' },
  { to: 50, className: 'gc-tone-orange' },
  { to: 75, className: 'gc-tone-green' },
  { to: 95, className: 'gc-tone-blue' },
  { to: 100, className: 'gc-tone-purple' }
];
// 回饋代碼的中文只收錄對照過 Garmin 網頁的；沒收錄的不顯示，不自己翻譯。
var READINESS_FEEDBACK_LABEL = { RECOVERED_AND_READY: '已恢復且準備好', WELL_RESTED: '充分休息' };
// 訓練完備程度的「因素」評級：Garmin 代碼 → 語氣（圖示形狀與顏色，與 HRV 狀態同一套）。
// 只分語氣，不代表本專案的判讀。
var READINESS_FACTOR_TONE = { VERY_GOOD: 'good', GOOD: 'good', MODERATE: 'warn', POOR: 'bad', VERY_POOR: 'bad' };
// 各因素右側的中文：只收錄對照過 Garmin 網頁的組合（2026-10-23 Fu 截圖）；沒收錄的只顯示圖示，
// 代碼放在滑鼠提示，不自己翻譯。看到新的組合再補。
var READINESS_FACTOR_TEXT = {
  sleep: { GOOD: '良好' },
  recovery: { GOOD: '已恢復' },
  load: { GOOD: '極佳' },
  sleep_history: { GOOD: '良好', MODERATE: '普通' },
  stress: { GOOD: '低' }
};
// 睡眠分數區間：Garmin 公布的分級（90–100 優異、80–89 良好、60–79 一般、0–59 差）。
// 由分數在前端對照，不是 API 回傳的評級。
var SLEEP_SCORE_BANDS = [[90, '優異'], [80, '良好'], [60, '一般'], [0, '差']];
function sleepScoreBand(score) {
  if (typeof score !== 'number') return '';
  for (var i = 0; i < SLEEP_SCORE_BANDS.length; i++) {
    if (score >= SLEEP_SCORE_BANDS[i][0]) return SLEEP_SCORE_BANDS[i][1];
  }
  return '';
}
// 耐力分數分級（共 7 級）。只收錄對照過網頁的 3＝進階，其餘顯示級數。
var ENDURANCE_CLASS_LABEL = { 3: '進階' };
// 訓練狀態色條的配色：只分語氣，不代表本專案的判讀
var TRAINING_STATUS_TONE = {
  PEAKING: 'gc-tone-purple', PRODUCTIVE: 'gc-tone-green', MAINTAINING: 'gc-tone-yellow',
  RECOVERY: 'gc-tone-blue', UNPRODUCTIVE: 'gc-tone-orange', DETRAINING: 'gc-tone-gray',
  OVERREACHING: 'gc-tone-red', STRAINED: 'gc-tone-red'
};
// 壓力分級：Garmin 的 0–25 休息、26–50 低、51–75 中、76–100 高
function stressTone(v) {
  if (v <= 25) return 'gc-stress-rest';
  if (v <= 50) return 'gc-stress-low';
  if (v <= 75) return 'gc-stress-mid';
  return 'gc-stress-high';
}

var WEEKDAY = ['日', '一', '二', '三', '四', '五', '六'];

function num(v, decimals) {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  return Number(v).toLocaleString('zh-TW', { maximumFractionDigits: decimals || 0, minimumFractionDigits: decimals || 0 });
}

function hm(sec) {
  if (!sec) return '—';
  return Math.floor(sec / 3600) + '時' + Math.round((sec % 3600) / 60) + '分';
}

function clock(iso) {
  return iso ? String(iso).slice(11, 16) : '';
}

function monthDay(iso) {
  if (!iso) return '';
  var p = String(iso).split('-');
  return parseInt(p[1], 10) + '月' + parseInt(p[2], 10) + '日';
}

/** 卡片底下「資料日期」：不是今天時要寫出來，免得把昨天的值當今天。 */
function dateNote(card) {
  return card.date && card.date !== todayISO() ? C.shortDate(card.date) + ' 的資料' : '';
}

function minuteToClock(m) {
  return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
}

/** 主數字＋單位 */
function bigValue(value, unit, className) {
  var wrap = h('div', 'gc-value ' + (className || ''));
  wrap.appendChild(h('span', 'gc-value-num', value));
  if (unit) wrap.appendChild(h('span', 'gc-value-unit', unit));
  return wrap;
}

/** 量表（環或弧）中央疊一個數字 */
function gaugeWithCenter(svg, centerText) {
  var wrap = h('div', 'gc-gauge-wrap');
  wrap.appendChild(svg);
  wrap.appendChild(h('span', 'gc-gauge-center', centerText));
  return wrap;
}

/**
 * 卡片上方的「左大右小」數值對（心率、HRV、睡眠共用）：數字大、單位小，下方各一行灰字說明。
 * 每側 {value, unit, caption, title, small, compact}；右側可省略。右側預設比左側小一級，
 * small 讓左側也縮小一級（上下堆疊的數值用），compact 再縮小一級。
 */
function metricCell(m, isSecondary) {
  var cell = h('div', 'gc-lead' + (isSecondary ? ' gc-lead-end' : ''));
  var size = (isSecondary || m.small ? 'gc-value-sm ' : '') + (m.compact ? 'gc-value-md' : '');
  cell.appendChild(bigValue(m.value, m.unit, size));
  var caption = h('div', 'gc-caption', m.caption || '');
  if (m.title) caption.title = m.title;
  cell.appendChild(caption);
  return cell;
}

function metricPair(primary, secondary) {
  var row = h('div', 'gc-split');
  if (primary) row.appendChild(metricCell(primary, false));
  if (secondary) row.appendChild(metricCell(secondary, true));
  return row;
}

function labelled(value, label) {
  var cell = h('div', 'gc-stat');
  cell.appendChild(h('div', 'gc-stat-value', value));
  cell.appendChild(h('div', 'gc-stat-label', label));
  return cell;
}

function sparkFrom(series, key) {
  var svg = C.sparkline((series || []).map(function (p) { return p[key]; }));
  if (!svg) return null;
  var wrap = h('div', 'gc-spark');
  wrap.appendChild(svg);
  return wrap;
}

/**
 * 近 7 天每天是否達標（步數、樓層）：達標打 ✓，否則寫星期幾；最後一天粗體。
 * @param {Array} items [{date, value, goal}]——goal 用當天的目標（Garmin 的步數目標每天自動調整）
 */
function weekChecks(items) {
  var last7 = (items || []).slice(-7);
  if (!last7.length) return null;
  var row = h('div', 'gc-checks');
  last7.forEach(function (p, i) {
    var met = p.goal && p.value !== null && p.value >= p.goal;
    var d = new Date(p.date + 'T00:00:00');
    var cell = h('span', 'gc-check' + (met ? ' is-met' : '') + (i === last7.length - 1 ? ' is-today' : ''),
      met ? '✓' : WEEKDAY[d.getDay()]);
    cell.title = p.date + '：' + num(p.value) + (p.goal ? ' / ' + num(p.goal) : '');
    row.appendChild(cell);
  });
  return row;
}

/**
 * 24 小時日內圖。date 是今天時，在最後一個資料點畫虛線、之後到 24:00 畫點狀基線並註明資料時間——
 * 今天還沒過完、也還沒同步到的時段，不能看起來像「這段時間沒資料」。
 */
function intraday(points, opts, date, hideUpdateNote) {
  var last = (points || []).length ? points[points.length - 1][0] : null;
  var pending = date === todayISO() && last !== null && last < 1439;
  var svg = C.intradayChart(points, pending ? Object.assign({ pendingFrom: last + 1 }, opts) : opts);
  if (!svg) return null;
  var wrap = h('div', 'gc-intraday-wrap');
  wrap.appendChild(svg);
  var axis = h('div', 'gc-axis');
  axis.appendChild(h('span', null, '00:00'));
  axis.appendChild(h('span', null, '24:00'));
  wrap.appendChild(axis);
  // 說明自成一行：塞在 00:00 與 24:00 之間會在窄卡片上被截斷
  if (pending && !hideUpdateNote) wrap.appendChild(h('div', 'gc-axis-pending', '最後更新時間 ' + minuteToClock(last)));
  return wrap;
}

// ------------------------------------------------------------ 各卡內容

function renderHrv(c, body) {
  var status = h('div', 'gc-status');
  var mark = hrvStatusMark(c.status);
  if (mark) status.appendChild(mark);
  status.appendChild(h('span', null, HRV_STATUS_LABEL[c.status] || c.status || '—'));
  // 由上往下一路排：狀態、平衡區間量表、7 天平均、夜間平均（數值在上、灰字說明在下）
  body.appendChild(status);
  if (c.baseline_balanced_low && c.baseline_balanced_upper) {
    body.appendChild(hrvRangeGauge(c.weekly_avg_ms, c.baseline_balanced_low, c.baseline_balanced_upper));
  }
  var averages = h('div', 'gc-split gc-hrv-averages');
  averages.appendChild(metricCell({ value: num(c.weekly_avg_ms), unit: 'ms', caption: '7 天平均', small: true }, false));
  if (c.last_night_ms !== null) {
    averages.appendChild(metricCell({ value: num(c.last_night_ms), unit: 'ms', caption: '夜間平均' }, true));
  }
  body.appendChild(averages);
  return hrvTrend(c.series);
}

/** HRV 趨勢：灰色區間帶＋7 天平均點，下方標註涵蓋期間（仿 Garmin 網頁） */
function hrvTrend(series) {
  var svg = C.hrvBandChart((series || []).map(function (p) {
    return {
      date: p.date, value: p.hrv_weekly_avg_ms,
      low: p.hrv_baseline_balanced_low, upper: p.hrv_baseline_balanced_upper
    };
  }));
  if (!svg) return null;
  var wrap = h('div', 'gc-spark');
  wrap.title = '過去 4 週：灰帶＝每日平衡區間，圓點＝每日 7 天平均';
  wrap.appendChild(svg);
  return wrap;
}

/**
 * Garmin 風格的 HRV 平衡區間量表：一條橫軸，綠色帶是平衡區間，圓點是 7 日均值。
 * 軸範圍取區間寬度的一半往兩側外推，圓點超出時貼齊邊緣。只畫位置，不下判讀。
 */
function hrvRangeGauge(weeklyAvg, low, upper) {
  var span = upper - low;
  var min = Math.max(0, low - span * 0.5);
  var max = upper + span * 0.5;
  function pct(v) { return Math.min(100, Math.max(0, (v - min) / (max - min) * 100)); }
  var gauge = h('div', 'hrv-gauge');
  var track = h('div', 'hrv-gauge-track');
  var band = h('div', 'hrv-gauge-band');
  band.style.left = pct(low) + '%';
  band.style.width = (pct(upper) - pct(low)) + '%';
  track.appendChild(band);
  if (typeof weeklyAvg === 'number') {
    var dot = h('div', 'hrv-gauge-dot');
    dot.style.left = pct(weeklyAvg) + '%';
    track.appendChild(dot);
  }
  gauge.appendChild(track);
  var scale = h('div', 'hrv-gauge-scale');
  scale.appendChild(h('span', null, num(low)));
  scale.appendChild(h('span', null, num(upper)));
  gauge.appendChild(scale);
  return gauge;
}

/**
 * 因素清單（Garmin 網頁的「因素」）：名稱｜數值｜評級文字＋形狀圖示。
 * 評級是 Garmin 的黑盒輸出，只供參考；舊的日子沒有評級時整塊不顯示。
 */
function present(v) { return v !== null && v !== undefined; }

function readinessFactors(c) {
  var rows = [
    {
      key: 'sleep', label: '睡眠分數', feedback: c.sleep_feedback,
      value: present(c.sleep_score) ? num(c.sleep_score) : ''
    },
    {
      key: 'recovery', label: '恢復時間', feedback: c.recovery_feedback, primary: true,
      value: present(c.recovery_time_hours) ? Math.round(c.recovery_time_hours) + ' 時' : ''
    },
    {
      key: 'hrv', label: 'HRV 狀態', feedback: c.hrv_feedback,
      value: present(c.hrv_weekly_avg_ms) ? num(c.hrv_weekly_avg_ms) + ' ms' : '',
      text: HRV_STATUS_LABEL[c.hrv_status]
    },
    {
      key: 'load', label: '短期負荷', feedback: c.load_feedback,
      value: present(c.acute_load) ? num(c.acute_load) : ''
    },
    { key: 'sleep_history', label: '近期睡眠', feedback: c.sleep_history_feedback, primary: true, value: '' },
    { key: 'stress', label: '近期壓力', feedback: c.stress_feedback, value: '' }
  ].filter(function (r) { return r.feedback; });
  if (!rows.length) return null;
  var list = h('div', 'gc-factors');
  // 預設只顯示兩項主要因素（恢復時間、近期睡眠），其餘收進「更多因素」，避免這張卡把同列的卡片撐高
  var hasExtra = false;
  rows.forEach(function (r) {
    var row = h('div', 'gc-factor');
    row.appendChild(h('span', 'gc-factor-label', r.label));
    row.appendChild(h('span', 'gc-factor-value', r.value));
    var verdict = h('span', 'gc-factor-verdict');
    var text = r.text || (READINESS_FACTOR_TEXT[r.key] || {})[r.feedback] || '';
    if (text) verdict.appendChild(h('span', null, text));
    var mark = toneMark(READINESS_FACTOR_TONE[r.feedback] || 'muted');
    if (mark) verdict.appendChild(mark);
    row.title = 'Garmin 評級：' + r.feedback;
    row.appendChild(verdict);
    if (!r.primary) {
      row.classList.add('gc-factor-extra');
      hasExtra = true;
    }
    list.appendChild(row);
  });
  if (hasExtra) {
    // 展開後次要因素留在原本的順序裡，切換鈕固定在清單最下方
    var toggle = h('button', 'gc-factors-toggle', '更多因素');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', function () {
      var open = list.classList.toggle('is-expanded');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      toggle.textContent = open ? '收合' : '更多因素';
    });
    list.appendChild(toggle);
  }
  return list;
}

function renderReadiness(c, body) {
  var gauge = gaugeWithCenter(C.bandArc(c.score, { min: 0, max: 100, bands: READINESS_BANDS }), num(c.score));
  gauge.classList.add('gc-gauge-readiness');
  body.appendChild(gauge);
  if (c.level) body.appendChild(h('div', 'gc-center-label', READINESS_LEVEL_LABEL[c.level] || c.level));
  var feedback = READINESS_FEEDBACK_LABEL[c.feedback_short];
  if (feedback) body.appendChild(h('div', 'gc-center-caption', feedback));
  var factors = readinessFactors(c);
  if (factors) body.appendChild(factors);
  // 序列的 key 是欄位名 training_readiness_score（見 garmin_cards._series），不是卡片上的 score
  return sparkFrom(c.series, 'training_readiness_score');
}

function renderSleep(c, body) {
  body.appendChild(metricPair(
    {
      value: num(c.score), caption: sleepScoreBand(c.score),
      title: '依 Garmin 公布的睡眠分數區間對照（非 Garmin 回傳的評級）'
    },
    { value: hm(c.duration_sec), caption: '持續時間', compact: true }));
  var hyp = C.hypnogram(c.levels);
  if (hyp) {
    var wrap = h('div', 'gc-intraday-wrap');
    wrap.appendChild(hyp);
    var axis = h('div', 'gc-axis');
    axis.appendChild(h('span', null, clock(c.start_local)));
    axis.appendChild(h('span', null, clock(c.end_local)));
    wrap.appendChild(axis);
    body.appendChild(wrap);
  }
  return sparkFrom(c.series, 'sleep_score');
}

function renderSteps(c, body) {
  body.appendChild(gaugeWithCenter(C.ringGauge(c.goal ? c.value / c.goal : 0, { className: 'gc-tone-blue' }), num(c.value)));
  if (c.goal) body.appendChild(h('div', 'gc-center-caption', '目標 ' + num(c.goal)));
  var checks = weekChecks((c.series || []).map(function (p) {
    return { date: p.date, value: p.steps, goal: p.steps_goal };
  }));
  if (checks) body.appendChild(checks);
  return null;
}

function renderTrainingStatus(c, body) {
  var label = GARMIN_TRAINING_STATUS_LABEL[c.status] || c.status;
  var badge = h('div', 'gc-status-badge ' + (TRAINING_STATUS_TONE[c.status] || 'gc-tone-gray'), '▲');
  badge.setAttribute('aria-hidden', 'true');
  // 圖示、狀態、起始日包成一組：首頁在色條上方的空間裡垂直置中（見 .gc-status-main）
  var main = h('div', 'gc-status-main');
  main.appendChild(badge);
  main.appendChild(h('div', 'gc-status-text', label));
  if (c.since) main.appendChild(h('div', 'gc-caption', '自 ' + monthDay(c.since)));
  body.appendChild(main);
  // 近 4 週色條：一天一格
  var strip = h('div', 'gc-strip');
  (c.series || []).slice(-28).forEach(function (p) {
    var cell = h('span', 'gc-strip-cell ' + (TRAINING_STATUS_TONE[p.status] || 'gc-tone-gray'));
    cell.title = p.date + '：' + (GARMIN_TRAINING_STATUS_LABEL[p.status] || p.status);
    strip.appendChild(cell);
  });
  if (strip.childNodes.length) body.appendChild(strip);
  return null;
}

/** 排法照 Garmin 網頁：圓環置中，下方左右兩欄「已充電／已消耗」，最底下是當日走勢。 */
function renderBodyBattery(c, body) {
  var value = c.latest !== null ? c.latest : c.high;
  body.appendChild(gaugeWithCenter(C.ringGauge(value / 100, { className: 'gc-tone-blue' }), num(value)));
  var stats = h('div', 'gc-stats gc-stats-ends');
  if (c.charged !== null) stats.appendChild(labelled('+' + num(c.charged), '已充電'));
  if (c.drained !== null) stats.appendChild(labelled('−' + num(c.drained), '已消耗'));
  if (stats.childNodes.length) body.appendChild(stats);
  var chart = intraday(c.intraday, { mode: 'area', min: 0, max: 100 }, c.date);
  if (chart) body.appendChild(chart);
  return null;
}

function renderHeartRate(c, body) {
  // 兩個靜止心率上下排：並排時在窄卡裡太擠
  if (c.resting_7day_avg !== null) {
    body.appendChild(metricCell({ value: num(c.resting_7day_avg), unit: 'bpm', caption: '平均 7 天靜止', small: true }, false));
  }
  body.appendChild(metricCell({ value: num(c.resting), unit: 'bpm', caption: '靜止', small: true }, false));
  // 心率卡不寫「最後更新時間」：圖上的虛線已標出資料到哪裡
  var chart = intraday(c.intraday, { mode: 'line' }, c.date, true);
  if (chart) body.appendChild(chart);
  return sparkFrom(c.series, 'resting_hr_bpm');
}

function renderCalories(c, body) {
  var total = (c.active || 0) + (c.resting || 0);
  body.appendChild(bigValue(num(total), 'kcal'));
  var bar = h('div', 'gc-split-bar');
  var active = h('span', 'gc-split-bar-a');
  active.style.width = (total ? (c.active || 0) / total * 100 : 0) + '%';
  bar.appendChild(active);
  bar.appendChild(h('span', 'gc-split-bar-b'));
  body.appendChild(bar);
  var stats = h('div', 'gc-stats gc-stats-ends');
  stats.appendChild(labelled(num(c.active), '活躍'));
  stats.appendChild(labelled(num(c.resting), '靜態'));
  body.appendChild(stats);
  return null;
}

// 健康狀態的指標名稱與單位（順序由後端依 Garmin 網頁排好）
var HEALTH_METRIC = {
  HR: { label: '心率', unit: 'bpm', decimals: 0 },
  HRV: { label: 'HRV', unit: 'ms', decimals: 0 },
  RESPIRATION: { label: '呼吸', unit: 'brpm', decimals: 1 },
  SPO2: { label: '脈搏血氧', unit: '%', decimals: 0 },
  SKIN_TEMP_C: { label: '皮膚溫度', unit: '°C', decimals: 1 }
};
// Garmin 的判定代碼（2026-10-22 實測只有這三種）
var HEALTH_STATUS_LABEL = { IN_RANGE: '在範圍內', BELOW: '低於範圍', ABOVE: '高於範圍' };

/**
 * 健康狀態：上方總結（範圍內／N 項超出範圍），下方 2×2 格，每格一個指標的弧形量表，
 * 量表中央是數值、下方是單位與 Garmin 給的個人範圍。四項一眼看完，不必逐列讀。
 */
function renderHealthStatus(c, body) {
  // 「範圍內」對照 Garmin 網頁；有超出時寫出項數
  body.appendChild(h('div', 'gc-status-text gc-status-xl' + (c.outliers ? ' is-out' : ''),
    c.outliers ? c.outliers + ' 項超出範圍' : '範圍內'));
  var grid = h('div', 'gc-health-grid');
  (c.metrics || []).forEach(function (m) {
    var def = HEALTH_METRIC[m.metric] || { label: m.metric, unit: '', decimals: 1 };
    var out = m.status !== 'IN_RANGE';
    var cell = h('div', 'gc-health-cell' + (out ? ' is-out' : ''));
    cell.appendChild(h('div', 'gc-health-label', def.label));
    // 範圍內時圓點位置用 Garmin 的 percentage；超出時 percentage 不代表位置，貼齊該側邊緣
    var pos = m.status === 'BELOW' ? 0 : m.status === 'ABOVE' ? 100 : m.percentage;
    var gauge = gaugeWithCenter(
      C.bandArc(pos, { min: 0, max: 100, bands: [{ to: 100, className: out ? 'gc-tone-orange' : 'gc-tone-blue' }] }),
      num(m.value, def.decimals));
    gauge.classList.add('gc-health-gauge');
    cell.appendChild(gauge);
    // 單位（超出時改寫判定）與範圍分兩行：擠在一行時「高於範圍 13.9–16.7」會撐破手機上的格子
    cell.appendChild(h('div', 'gc-health-range', out ? (HEALTH_STATUS_LABEL[m.status] || m.status) : def.unit));
    cell.appendChild(h('div', 'gc-health-range', num(m.lower, def.decimals) + '–' + num(m.upper, def.decimals)));
    cell.title = def.label + ' ' + num(m.value, def.decimals) + ' ' + def.unit + '：'
      + (HEALTH_STATUS_LABEL[m.status] || m.status) + '（個人範圍 '
      + num(m.lower, def.decimals) + '–' + num(m.upper, def.decimals) + '）';
    grid.appendChild(cell);
  });
  body.appendChild(grid);
  return null;
}

function renderStress(c, body) {
  body.appendChild(gaugeWithCenter(C.ringGauge(c.avg / 100, { className: stressTone(c.avg) }), num(c.avg)));
  var chart = intraday(c.intraday, { mode: 'bars', min: 0, max: 100, barClass: stressTone }, c.date);
  if (chart) body.appendChild(chart);
  return null;
}

function renderIntensity(c, body) {
  var frac = c.weekly_goal ? c.week_total / c.weekly_goal : 0;
  body.appendChild(gaugeWithCenter(C.ringGauge(frac, { className: 'gc-tone-green' }), num(c.week_total)));
  if (c.weekly_goal) body.appendChild(h('div', 'gc-center-caption', '本週目標 ' + num(c.weekly_goal)));
  // 本週每天的分鐘數（高強度計兩倍），週一到週日
  var row = h('div', 'gc-checks');
  var byDate = {};
  (c.daily || []).forEach(function (d) { byDate[d.date] = (d.moderate || 0) + 2 * (d.vigorous || 0); });
  var monday = new Date(c.week_start + 'T00:00:00');
  for (var i = 0; i < 7; i += 1) {
    var d = new Date(monday);
    d.setDate(monday.getDate() + i);
    var iso = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    var mins = byDate[iso];
    var cell = h('span', 'gc-check' + (mins ? ' is-met' : '') + (iso === c.date ? ' is-today' : ''),
      mins !== undefined ? String(mins) : WEEKDAY[d.getDay()]);
    cell.title = iso + (mins !== undefined ? '：' + mins + ' 分鐘' : '');
    row.appendChild(cell);
  }
  body.appendChild(row);
  return null;
}

function renderFloors(c, body) {
  body.appendChild(gaugeWithCenter(C.ringGauge(c.goal ? c.value / c.goal : 0, { className: 'gc-tone-blue' }), num(Math.floor(c.value))));
  if (c.goal) body.appendChild(h('div', 'gc-center-caption', '目標 ' + num(c.goal)));
  // 樓層目標的歷史值沒有放進序列，沿用最新目標（Garmin 的樓層目標不會自動調整）
  var checks = weekChecks((c.series || []).map(function (p) {
    return { date: p.date, value: p.floors_ascended, goal: c.goal };
  }));
  if (checks) body.appendChild(checks);
  return null;
}

function renderSpo2(c, body) {
  var shown = c.latest !== null ? c.latest : c.avg;
  body.appendChild(bigValue(num(shown), '%'));
  var stats = h('div', 'gc-stats');
  if (c.avg !== null) stats.appendChild(labelled(num(c.avg) + '%', '平均'));
  if (c.lowest !== null) stats.appendChild(labelled(num(c.lowest) + '%', '最低'));
  body.appendChild(stats);
  return sparkFrom(c.series, 'spo2_pct');
}

function renderVo2max(c, body) {
  var r = c.rating;
  if (r && r.cutoffs) {
    var lo = r.cutoffs[0] - 8;
    var hi = r.cutoffs[3] + 6;
    var bands = [
      { to: r.cutoffs[0], className: 'gc-tone-red' },
      { to: r.cutoffs[1], className: 'gc-tone-orange' },
      { to: r.cutoffs[2], className: 'gc-tone-green' },
      { to: r.cutoffs[3], className: 'gc-tone-blue' },
      { to: hi, className: 'gc-tone-purple' }
    ];
    body.appendChild(gaugeWithCenter(C.bandArc(c.value, { min: lo, max: hi, bands: bands }), num(c.value)));
    body.appendChild(h('div', 'gc-center-label', r.label));
  } else {
    body.appendChild(bigValue(num(c.value, 1)));
  }
  return sparkFrom(c.series, 'vo2max_precise');
}

function renderRespiration(c, body) {
  var stats = h('div', 'gc-stack');
  if (c.waking_avg !== null) stats.appendChild(labelled(num(c.waking_avg) + ' brpm', '清醒平均'));
  if (c.sleep_avg !== null) stats.appendChild(labelled(num(c.sleep_avg) + ' brpm', '睡眠平均'));
  if (c.low !== null && c.high !== null) stats.appendChild(labelled(num(c.low) + '/' + num(c.high) + ' brpm', '低/高'));
  body.appendChild(stats);
  return null;
}

function renderLactate(c, body) {
  var stats = h('div', 'gc-stack');
  if (c.hr_bpm !== null) stats.appendChild(labelled(num(c.hr_bpm) + ' bpm', '心率'));
  if (c.pace_sec_per_km !== null) stats.appendChild(labelled(C.formatPace(c.pace_sec_per_km) + ' /公里', '配速'));
  if (c.power_watts !== null) stats.appendChild(labelled(num(c.power_watts) + ' 瓦', '功率'));
  body.appendChild(stats);
  return null;
}

function renderEndurance(c, body) {
  body.appendChild(bigValue(num(c.score)));
  if (c.classification !== null) {
    body.appendChild(h('div', 'gc-center-label',
      ENDURANCE_CLASS_LABEL[c.classification] || '第 ' + c.classification + ' 級（共 7 級）'));
  }
  return sparkFrom(c.series, 'endurance_score');
}

function renderTrainingLoad(c, body) {
  if (c.ratio_status) body.appendChild(h('div', 'gc-status-text', GARMIN_ACWR_STATUS_LABEL[c.ratio_status] || c.ratio_status));
  body.appendChild(bigValue(num(c.acute) + '/' + num(c.chronic)));
  body.appendChild(h('div', 'gc-caption', '短期/長期負荷'));
  if (c.ratio !== null) {
    // 標籤與數字同一行，省下一行高度（首頁兩排卡片要壓在 230px 內）
    var ratioRow = h('div', 'gc-ratio-row');
    ratioRow.appendChild(h('span', 'gc-caption', '負荷比'));
    ratioRow.appendChild(bigValue(num(c.ratio, 1), null, 'gc-value-sm'));
    body.appendChild(ratioRow);
  }
  // 短期負荷的走勢，與首頁「近期疲勞 ATL」卡同一種迷你趨勢線
  return sparkFrom(c.series, 'acute_load');
}

// 負荷焦點三類，由上而下的順序與配色對照 Garmin 網頁（2026-10-22 Fu 截圖）：
// 高強度有氧亮橘、低強度有氧青藍；無氧在截圖中太短看不出顏色，暫用紫色
var LOAD_FOCUS_LABEL = { anaerobic: '無氧', aerobic_high: '高強度有氧', aerobic_low: '低強度有氧' };
var LOAD_FOCUS_TONE = { anaerobic: 'gc-focus-anaerobic', aerobic_high: 'gc-focus-high', aerobic_low: 'gc-focus-low' };
// 回饋代碼的中文只收錄對照過 Garmin 網頁的，沒收錄的不顯示
var LOAD_FOCUS_FEEDBACK_LABEL = { LOW_AEROBIC_OVERAGE: '低強度有氧過量' };
// 負荷焦點涵蓋的天數（Garmin 網頁顯示「9月24日 - 10月22日」，即含當天往回 4 週）
var LOAD_FOCUS_DAYS = 28;

function renderLoadFocus(c, body) {
  var feedback = LOAD_FOCUS_FEEDBACK_LABEL[c.feedback];
  if (feedback) body.appendChild(h('div', 'gc-status-text', feedback));
  var cats = (c.categories || []).filter(function (x) { return x.value !== null; });
  // 三條共用同一個刻度，才看得出彼此的比例；刻度上限留一點空間給超出目標的長條
  var scale = Math.max.apply(null, cats.map(function (x) { return Math.max(x.value, x.target_max || 0); })) * 1.1 || 1;
  cats.forEach(function (x) {
    var row = h('div', 'gc-focus-row');
    var head = h('div', 'gc-focus-head');
    head.appendChild(h('span', 'gc-stat-label', LOAD_FOCUS_LABEL[x.key] || x.key));
    head.appendChild(h('span', 'gc-focus-value', num(x.value)));
    row.appendChild(head);
    var track = h('div', 'gc-focus-track');
    if (x.target_min !== null && x.target_max !== null) {
      var target = h('span', 'gc-focus-target');
      target.style.left = (x.target_min / scale * 100) + '%';
      target.style.width = ((x.target_max - x.target_min) / scale * 100) + '%';
      target.title = '目標 ' + num(x.target_min) + '–' + num(x.target_max);
      track.appendChild(target);
    }
    var fill = h('span', 'gc-focus-fill ' + LOAD_FOCUS_TONE[x.key]);
    fill.style.width = Math.min(100, x.value / scale * 100) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    body.appendChild(row);
  });
  var legend = h('div', 'gc-focus-legend');
  legend.appendChild(h('span', 'gc-focus-legend-box'));
  legend.appendChild(h('span', null, '最佳範圍'));
  body.appendChild(legend);
  if (c.date) {
    var end = new Date(c.date + 'T00:00:00');
    var start = new Date(end);
    start.setDate(end.getDate() - LOAD_FOCUS_DAYS);
    var iso = function (d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
    body.appendChild(h('div', 'gc-caption', monthDay(iso(start)) + ' - ' + monthDay(c.date)));
  }
  return null;
}

/**
 * 卡片定義（唯一來源）：key 對應 /api/garmin-cards 的 cards.<key>，順序即 Garmin 網頁的排法。
 * tip 只講「這是什麼、怎麼來的」，不寫好壞。
 */
export var GARMIN_CARD_DEFS = [
  {
    key: 'hrv', label: 'HRV 狀態', render: renderHrv,
    tip: ['Garmin 依睡眠期間的心率變異度（RMSSD）算出 7 天平均，對照它推算的個人平衡區間判定狀態。',
      '量表的綠帶是平衡區間，圓點是 7 天平均。',
      '最下方是過去 4 週的趨勢：灰帶是每天的平衡區間，圓點是每天的 7 天平均。']
  },
  {
    key: 'training_readiness', label: '訓練完備程度', render: renderReadiness,
    tip: ['Garmin 的訓練準備度：綜合睡眠、恢復時間、HRV 與近期負荷的 0–100 分數，演算法未公開。',
      '取起床時的那一筆。與首頁的「綜合整備狀態」（本專案規則引擎）是兩回事。']
  },
  {
    key: 'sleep', label: '睡眠分數', render: renderSleep,
    tip: ['Garmin 依睡眠時長、各階段比例、壓力與中斷次數算出的 0–100 分數，演算法未公開。',
      '下方是睡眠階段：由低到高為深眠、淺眠、REM、清醒。']
  },
  {
    key: 'steps', label: '步數總計', render: renderSteps,
    tip: ['當日步數與 Garmin 設定的每日目標；下方為近 7 天，達標的日子打勾。']
  },
  {
    key: 'training_status', label: '訓練狀態', render: renderTrainingStatus,
    tip: ['Garmin 綜合 VO2max 走勢與訓練負荷給出的狀態分類；「自何時起」是 Garmin 給的日期。',
      '下方色條是近 4 週每天的狀態。']
  },
  {
    key: 'body_battery', label: '身體能量指數', render: renderBodyBattery,
    tip: ['Garmin 依 HRV、壓力與活動推估的 0–100 能量值，演算法未公開。下方為當日走勢。']
  },
  {
    key: 'heart_rate', label: '心率', render: renderHeartRate,
    tip: ['靜止心率與 Garmin 算的近 7 天靜止心率平均；下方為當日心率走勢。']
  },
  {
    key: 'calories', label: '燃燒的卡路里', render: renderCalories,
    tip: ['活躍卡路里（活動消耗）與靜態卡路里（基礎代謝），皆為 Garmin 的推估。']
  },
  {
    key: 'health_status', label: '健康狀態', render: renderHealthStatus,
    tip: ['Garmin 把睡眠期間的心率、HRV、呼吸與血氧，和它依你近幾週資料推算的個人範圍比較。',
      '範圍與判定都是 Garmin 給的（不是一般人的健康標準，而是「你平常落在哪裡」）；本頁不自行計算。']
  },
  {
    key: 'stress', label: '壓力', render: renderStress,
    tip: ['Garmin 依 HRV 推估的全天平均壓力（0–100）。0–25 休息、26–50 低、51–75 中、76–100 高。',
      '下方為當日每 3 分鐘的壓力值；活動中無法量測的時段留白。']
  },
  {
    key: 'intensity_minutes', label: '熱血時間', render: renderIntensity,
    tip: ['中高強度活動的分鐘數。本週累計依 Garmin 的規則計算：週一起算、高強度分鐘數計兩倍。']
  },
  {
    key: 'floors', label: '樓層', render: renderFloors,
    tip: ['手錶氣壓計偵測的爬升樓層數與每日目標。']
  },
  {
    key: 'spo2', label: '脈搏血氧', render: renderSpo2,
    tip: ['手錶量測的血氧飽和度：主數字為最新一筆，另列當日平均與最低。']
  },
  {
    key: 'vo2max', label: '跑步最大攝氧量', render: renderVo2max,
    tip: ['Garmin 推估的跑步 VO2max（ml/kg/min）。',
      '分級不是 Garmin API 提供的：本頁依 Garmin 網站刊載的 Cooper Institute 分級表，按你的年齡與性別查得'
      + '（優異＝前 5%、傑出＝前 20%、良好＝前 40%、普通＝前 60%）。']
  },
  {
    key: 'respiration', label: '呼吸', render: renderRespiration,
    tip: ['每分鐘呼吸次數：清醒時平均、睡眠時平均，以及當日最低／最高。']
  },
  {
    key: 'lactate_threshold', label: '跑步乳酸閾值', render: renderLactate,
    tip: ['Garmin 偵測的乳酸閾值心率、配速與功率。']
  },
  {
    key: 'endurance_score', label: '耐力分數', render: renderEndurance,
    tip: ['Garmin 綜合長期訓練量與強度的耐力評分與分級（共 7 級）。']
  },
  {
    key: 'training_load', label: '訓練負荷', render: renderTrainingLoad,
    tip: ['Garmin 的短期（7 天）與長期（4 週）訓練負荷及兩者比值，尺度與本專案的 TRIMP 不同。',
      '下方趨勢線是短期負荷的每日走勢。']
  },
  {
    key: 'load_focus', label: '負荷焦點', render: renderLoadFocus,
    tip: ['Garmin 把近 4 週的訓練負荷分成無氧、高強度有氧、低強度有氧三類，各自附上依你的訓練歷史推算的目標範圍。',
      '長條是實際負荷，淡色框是目標範圍；三條共用同一個刻度。']
  }
];

var DEF_BY_KEY = {};
GARMIN_CARD_DEFS.forEach(function (d) { DEF_BY_KEY[d.key] = d; });

// ------------------------------------------------------------ 趨勢頁 Garmin 區（E.2）

/**
 * 小格子分數一覽：每張卡濃縮成一行字（主數值＋必要的單位或分級）。
 * 與卡片同一份資料、同一組標籤對照，只是不畫圖。沒有資料回 null。
 */
var GARMIN_TILE_VALUE = {
  hrv: function (c) { return (HRV_STATUS_LABEL[c.status] || c.status || '') + ' ' + num(c.weekly_avg_ms) + ' ms'; },
  training_readiness: function (c) { return num(c.score) + (c.level ? ' ' + (READINESS_LEVEL_LABEL[c.level] || c.level) : ''); },
  sleep: function (c) { return num(c.score) + '（' + hm(c.duration_sec) + '）'; },
  steps: function (c) { return num(c.value); },
  training_status: function (c) { return GARMIN_TRAINING_STATUS_LABEL[c.status] || c.status || '—'; },
  body_battery: function (c) { return num(c.latest !== null ? c.latest : c.high); },
  heart_rate: function (c) { return num(c.resting) + ' bpm'; },
  calories: function (c) { return num((c.active || 0) + (c.resting || 0)) + ' kcal'; },
  health_status: function (c) { return c.outliers ? c.outliers + ' 項超出範圍' : '範圍內'; },
  stress: function (c) { return num(c.avg); },
  intensity_minutes: function (c) { return num(c.week_total) + ' 分（本週）'; },
  floors: function (c) { return num(Math.floor(c.value)); },
  spo2: function (c) { return num(c.latest !== null ? c.latest : c.avg) + '%'; },
  vo2max: function (c) { return num(c.value, 1) + (c.rating ? ' ' + c.rating.label : ''); },
  respiration: function (c) { return num(c.waking_avg) + ' brpm'; },
  lactate_threshold: function (c) { return c.hr_bpm !== null ? num(c.hr_bpm) + ' bpm' : '—'; },
  endurance_score: function (c) { return num(c.score); },
  training_load: function (c) { return num(c.acute) + '/' + num(c.chronic); },
  load_focus: function (c) {
    var low = (c.categories || []).filter(function (x) { return x.key === 'aerobic_low'; })[0];
    return LOAD_FOCUS_FEEDBACK_LABEL[c.feedback] || (low ? '低強度有氧 ' + num(low.value) : '—');
  }
};

/**
 * 分數一覽格線：GARMIN_CARD_DEFS 的每張卡一格（標籤＋一行數值），沒資料的卡顯示「—」。
 * @param {Object} data /api/garmin-cards 的回傳
 */
export function garminScoreTiles(data) {
  var cards = (data && data.cards) || {};
  var grid = h('div', 'gc-tiles');
  GARMIN_CARD_DEFS.forEach(function (d) {
    var c = cards[d.key];
    var tile = h('div', 'gc-tile' + (c ? '' : ' is-empty'));
    tile.appendChild(h('div', 'gc-tile-label', d.label));
    tile.appendChild(h('div', 'gc-tile-value', c ? GARMIN_TILE_VALUE[d.key](c) : '—'));
    if (c && c.date) tile.title = d.label + '：' + c.date + ' 的資料';
    grid.appendChild(tile);
  });
  return grid;
}

/**
 * 趨勢頁 Garmin 大折線圖可勾選的每日指標：card 是 /api/garmin-cards 的卡片 key，
 * field 是該卡 series 的欄位名（見 services/garmin_cards.py 的 _series）。
 * unit 決定共用哪個 Y 軸（同單位同軸）；★ 預設開啟。
 */
export var GARMIN_SERIES_DEFS = [
  { key: 'readiness', label: '訓練準備度', card: 'training_readiness', field: 'training_readiness_score', unit: '分數', color: 'var(--c-metric-training_readiness_score)', on: true },
  { key: 'bb_high', label: '身體能量（最高）', card: 'body_battery', field: 'body_battery_max', unit: '分數', color: 'var(--c-distance)', on: true },
  { key: 'bb_low', label: '身體能量（最低）', card: 'body_battery', field: 'body_battery_min', unit: '分數', color: 'var(--c-volume)' },
  { key: 'endurance', label: '耐力分數', card: 'endurance_score', field: 'endurance_score', unit: '耐力分數', color: 'var(--cat-ability-strong)' },
  { key: 'vo2max', label: '跑步最大攝氧量', card: 'vo2max', field: 'vo2max_precise', unit: 'ml/kg/min', color: 'var(--c-te)', decimals: 1 },
  { key: 'acute', label: '短期負荷', card: 'training_load', field: 'acute_load', unit: 'Garmin 負荷', color: 'var(--cat-load-strong)' },
  { key: 'chronic', label: '長期負荷', card: 'training_load', field: 'chronic_load', unit: 'Garmin 負荷', color: 'var(--c-pace)' },
  { key: 'hrv_weekly', label: 'HRV 7 天平均', card: 'hrv', field: 'hrv_weekly_avg_ms', unit: 'ms', color: 'var(--c-metric-hrv_weekly_avg_ms)' },
  { key: 'hrv_night', label: 'HRV 夜間平均', card: 'hrv', field: 'hrv_ms', unit: 'ms', color: 'var(--c-metric-hrv_ms)' },
  { key: 'sleep', label: '睡眠分數', card: 'sleep', field: 'sleep_score', unit: '分數', color: 'var(--c-metric-sleep_score)' },
  { key: 'stress', label: '壓力（全天）', card: 'stress', field: 'all_day_stress_avg', unit: '分數', color: 'var(--c-metric-all_day_stress_avg)' },
  { key: 'resting_hr', label: '靜止心率', card: 'heart_rate', field: 'resting_hr_bpm', unit: 'bpm', color: 'var(--c-metric-resting_hr_bpm)' },
  { key: 'steps', label: '步數', card: 'steps', field: 'steps', unit: '步', color: 'var(--c-metric-steps)' },
  { key: 'floors', label: '樓層', card: 'floors', field: 'floors_ascended', unit: '層', color: 'var(--c-metric-acwr)' },
  { key: 'calories_active', label: '活躍卡路里', card: 'calories', field: 'calories_active', unit: 'kcal', color: 'var(--c-hr)' },
  { key: 'calories_resting', label: '靜態卡路里', card: 'calories', field: 'calories_resting', unit: 'kcal', color: 'var(--c-metric-recovery_time_hours)' },
  { key: 'spo2', label: '脈搏血氧', card: 'spo2', field: 'spo2_pct', unit: '%', color: 'var(--c-metric-spo2_pct)' },
  { key: 'resp_waking', label: '呼吸（清醒）', card: 'respiration', field: 'respiration_rate', unit: 'brpm', color: 'var(--c-metric-respiration_rate)', decimals: 1 },
  { key: 'resp_sleep', label: '呼吸（睡眠）', card: 'respiration', field: 'respiration_sleep_avg', unit: 'brpm', color: 'var(--c-metric-stress_avg)', decimals: 1 }
];

/** GARMIN_SERIES_DEFS 一筆 → [{date, value}]（卡片不存在時為空陣列）。 */
export function garminSeriesPoints(data, def) {
  var card = data && data.cards && data.cards[def.card];
  return ((card && card.series) || []).map(function (p) {
    var v = p[def.field];
    return { date: p.date, value: typeof v === 'number' && isFinite(v) ? v : null };
  });
}

/**
 * 畫一張 Garmin 卡片到 node。cardData 為 /api/garmin-cards 的 cards[key]；沒有資料時顯示無資料。
 */
export function renderGarminCard(key, cardData, node) {
  var def = DEF_BY_KEY[key];
  clear(node);
  node.classList.add('gc-card', 'gc-card-' + key);
  var head = h('div', 'gc-head');
  head.appendChild(h('span', 'gc-title', def.label));
  if (def.tip) head.appendChild(infoTip(def.label, def.tip));
  node.appendChild(head);
  var body = h('div', 'gc-body');
  node.appendChild(body);
  if (!cardData) {
    body.appendChild(h('div', 'state-msg state-unavailable', '所選區間內沒有資料'));
    return;
  }
  var foot = def.render(cardData, body);
  var footer = h('div', 'gc-foot');
  if (foot) footer.appendChild(foot);
  var note = dateNote(cardData);
  if (note) footer.appendChild(h('div', 'gc-caption', note));
  if (footer.childNodes.length) node.appendChild(footer);
}

/**
 * 卡片牆：依 keys 順序畫進 node。data 為 /api/garmin-cards 的回傳；null 時顯示載入中。
 */
export function renderGarminWall(node, data, keys, loadingLabel) {
  if (!data) { showLoading(node, loadingLabel || '載入 Garmin 數據'); return; }
  clear(node);
  var cards = data.cards || {};
  var shown = keys.filter(function (k) { return DEF_BY_KEY[k]; });
  if (!shown.some(function (k) { return cards[k]; })) {
    showUnavailable(node, '所選區間內沒有 Garmin 數據');
    return;
  }
  var grid = h('div', 'gc-grid');
  shown.forEach(function (k) {
    var slot = h('div');
    renderGarminCard(k, cards[k], slot);
    grid.appendChild(slot);
  });
  node.appendChild(grid);
}
