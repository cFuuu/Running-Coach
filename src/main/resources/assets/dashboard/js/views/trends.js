/**
 * trends.js — 趨勢 view（E.2 改版，2026-10-24 定案；規格見 docs/dev/DASHBOARD_TASKS.md Task E.2）
 *
 * 五區由上而下：訓練負荷 → 恢復與身體 → 跑力表現 → 訓練量與強度 → Garmin 數據。
 * 前四區的結構相同：4 張數字卡（數值＋與前一段等長期間比較的 ▲▼＋小走勢線）→
 * 一列指標開關 → 一張多指標大圖（charts.multiLineChart）。開關涵蓋該類「改版前趨勢頁
 * 有的每個指標」，一個都不少；★ 預設開啟。訓練量與強度區另保留原本的心率區間分佈。
 * Garmin 數據區三層：可勾選的大折線圖 → 小格子分數一覽 → 首頁同款 Garmin 圖卡（garmin.js）。
 *
 * 前期比較：後端不改。依目前區間的實際起訖日（任一份回應的 start_date／end_date）推出
 * 前一段等長期間，以 range=custom:起:訖 另抓一次（離線匯出的 URL 清單也同步補上）。
 * 選「全部」時沒有起日，也就沒有前期，▲▼ 不顯示。
 *
 * 偏好：類別的順序與顯隱沿用 customize.js（偏好 key 'trends'）；Garmin 圖卡的順序與顯隱
 * 沿用 'trends-garmin'。各區開關、「標出訓練日」、Garmin 圖卡收合存在
 * rc-dashboard-settings 的 trendsToggles（讀寫一律經 theme.js）。改版前各類別內「圖表」的
 * 自訂（trends-load 等）已被開關列取代，不再讀取。
 *
 * 本頁只呈現不解讀（D.1）：數字卡的 ▲▼ 只標增減方向，不用紅綠，也不寫好壞。
 */

import * as C from '../charts.js';
import * as Theme from '../theme.js';
import { state, wellnessMetricDefs, CONFIDENCE_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showUnavailable, widthOf, wrapChart } from '../core/dom.js';
import { closeInfoPopover, kpiCard, tipSection } from '../core/facts.js';
import { recoveryHoursText } from '../core/format.js';
import { customizer, visibleDefs } from '../core/customize.js';
import { renderOrDefer } from '../shell/router.js';
import { loadSeriesToChartSeries, garminLoadNote, seriesFromSessions, SESSION_TRENDS } from './load.js';
import { wellnessFormatter, wellnessPoints, wellnessClipNote } from './wellness.js';
import { renderOverviewStrip, renderWeeklyVolume, renderRecentVolume, renderZones } from './overview.js';
import { GARMIN_CARD_DEFS, renderGarminCard, garminScoreTiles, GARMIN_SERIES_DEFS, garminSeriesPoints } from './garmin.js';
import { loadTip, confidenceTip } from './home.js';

// 歸在「訓練負荷」而非「恢復與身體」的 Garmin 每日指標
var LOAD_WELLNESS_KEYS = ['acwr', 'recovery_time_hours'];
// 恢復與身體預設開啟的指標
var BODY_DEFAULT_ON = ['hrv_ms', 'hrv_weekly_avg_ms'];

var DAY_MS = 86400000;

function isNum(v) { return typeof v === 'number' && isFinite(v); }
function dayMs(iso) { return Date.parse(iso + 'T00:00:00Z'); }
function isoOf(ms) { return new Date(ms).toISOString().slice(0, 10); }

function mean(values) {
  var nums = values.filter(isNum);
  if (!nums.length) return null;
  return nums.reduce(function (s, v) { return s + v; }, 0) / nums.length;
}

function lastValue(points) {
  for (var i = points.length - 1; i >= 0; i -= 1) if (isNum(points[i].value)) return points[i].value;
  return null;
}

function values(points) { return points.map(function (p) { return p.value; }); }

/** ISO 日期所在週的週一（課表與週跑量都以週一起算）。 */
function mondayOf(iso) {
  var ms = dayMs(iso);
  var dow = new Date(ms).getUTCDay();
  return isoOf(ms - ((dow + 6) % 7) * DAY_MS);
}

// ------------------------------------------------------------ 開關偏好（rc-dashboard-settings.trendsToggles）

function savedToggles() {
  var all = Theme.getSettings().trendsToggles;
  return (all && typeof all === 'object') ? all : {};
}

function saveToggle(key, value) {
  var next = {};
  var current = savedToggles();
  Object.keys(current).forEach(function (k) { next[k] = current[k]; });
  next[key] = value;
  Theme.saveSettings({ trendsToggles: next });
}

/** 某區目前開著的指標 key → true。從未調整過時用各指標的 on（★）。 */
function onKeys(section, defs) {
  var saved = savedToggles()[section];
  var on = {};
  if (Array.isArray(saved)) saved.forEach(function (k) { on[k] = true; });
  else defs.forEach(function (d) { if (d.on) on[d.key] = true; });
  return on;
}

function flag(key, fallback) {
  var v = savedToggles()[key];
  return typeof v === 'boolean' ? v : fallback;
}

// ------------------------------------------------------------ 前一段等長期間

/** 目前區間的實際起訖日：任一份已回來的回應（後端同一個 resolve_range）。 */
function currentWindow() {
  var sources = [state.wellness, state.loadSeries, state.overview];
  for (var i = 0; i < sources.length; i += 1) {
    var s = sources[i];
    if (s && s.end_date !== undefined) return { start: s.start_date, end: s.end_date };
  }
  return null;
}

/** 前一段等長期間；range=all（沒有起日）回 null。 */
export function previousWindow(win) {
  if (!win || !win.start || !win.end) return null;
  var days = Math.round((dayMs(win.end) - dayMs(win.start)) / DAY_MS) + 1;
  var end = dayMs(win.start) - DAY_MS;
  var start = end - (days - 1) * DAY_MS;
  return { start: isoOf(start), end: isoOf(end), range: 'custom:' + isoOf(start) + ':' + isoOf(end) };
}

var prevRequestedFor = null;

/** 目前區間的資料回來後，抓前一段等長期間（每個區間只抓一次）。 */
function ensurePrevious() {
  var win = currentWindow();
  if (!win) return;
  var prev = previousWindow(win);
  var tag = state.range + '|' + (prev ? prev.range : 'none');
  if (prevRequestedFor === tag) return;
  prevRequestedFor = tag;
  if (!prev) { state.trendsPrev = null; return; }
  var holder = { range: prev.range, start: prev.start, end: prev.end };
  state.trendsPrev = holder;
  function fetchInto(field, path, pick) {
    api(path + '?range=' + prev.range).then(function (data) {
      holder[field] = pick ? pick(data) : data;
    }).catch(function () {
      holder[field] = false; // 前期抓不到：數字卡只是不顯示 ▲▼
    }).then(function () {
      if (state.trendsPrev === holder) renderOrDefer('trends', renderTrendsPage);
    });
  }
  fetchInto('loadSeries', '/api/training-load');
  fetchInto('wellness', '/api/wellness-trend');
  fetchInto('sessions', '/api/sessions', function (d) { return (d && d.sessions) || []; });
  fetchInto('overview', '/api/overview');
}

/** 前期的某份資料：undefined＝載入中、false＝無法取得、null＝沒有前期（range=all）。 */
function prevData(field) {
  var p = state.trendsPrev;
  if (!p) return null;
  return p[field] === undefined ? undefined : p[field];
}

// ------------------------------------------------------------ 數字卡

/**
 * 與前期比較的中性標籤：▲／▼ 只代表數值變大或變小，不代表好壞（不用紅綠）。
 * @param {number|null} cur
 * @param {number|null|undefined} prev undefined＝前期還在載入
 */
function deltaParts(cur, prev, deltaFmt, fmt) {
  var p = state.trendsPrev;
  if (!p) return { badge: null, note: null };
  if (prev === undefined) return { badge: null, note: '前期載入中…' };
  if (!isNum(prev) || !isNum(cur)) return { badge: null, note: '前期無資料' };
  var d = cur - prev;
  var text = d > 0 ? '▲ ' + deltaFmt(Math.abs(d)) : (d < 0 ? '▼ ' + deltaFmt(Math.abs(d)) : '＝ 持平');
  var badge = h('span', 'neutral-badge trend-delta', text);
  badge.title = '與前一段等長期間（' + spanText(p.start, p.end) + '）比較，只標增減';
  return { badge: badge, note: '前期 ' + fmt(prev) };
}

/** 起訖日文字：同一年寫 M/D–M/D，跨年時兩端都帶年份（1 年區間的前期才不會看成同一天）。 */
function spanText(start, end) {
  if (start.slice(0, 4) === end.slice(0, 4)) return C.shortDate(start) + '–' + C.shortDate(end);
  return start.replace(/-/g, '/') + '–' + end.replace(/-/g, '/');
}

/**
 * @param {Object} o { label, abbr, value:number|null, fmt, unit, prev:number|null|undefined,
 *   deltaFmt?, series:Array<number>, invert?, note?, tip? }
 */
function trendKpi(o) {
  var delta = deltaParts(o.value, o.prev, o.deltaFmt || o.fmt, o.fmt);
  var notes = [o.note, delta.note].filter(Boolean);
  return kpiCard({
    label: o.label,
    abbr: o.abbr,
    value: isNum(o.value) ? o.fmt(o.value) : '—',
    unit: isNum(o.value) ? o.unit : null,
    badge: delta.badge,
    note: notes.join('｜'),
    series: o.series && o.series.filter(isNum).length > 1 ? o.series : null,
    invert: o.invert,
    tip: o.tip
  });
}

function kpiRow(cards) {
  var row = h('div', 'trend-kpis');
  cards.forEach(function (c) { row.appendChild(c); });
  return row;
}

/** 前期資料裡的值：前期不存在回 null，載入中回 undefined，否則交給 pick 計算。 */
function prevValue(field, pick) {
  var d = prevData(field);
  if (d === undefined) return undefined;
  if (!d) return null;
  return pick(d);
}

// ------------------------------------------------------------ 開關列與大圖

/**
 * 一列指標開關（chip）。點一下切換、存偏好、只重畫該區。
 * @param {Array} defs [{key, label, color, kind?}]
 * @param {Object} on key → true
 */
function chipRow(section, label, defs, on, rerender) {
  var row = h('div', 'trend-chips');
  row.setAttribute('role', 'group');
  row.setAttribute('aria-label', label + '：要畫哪些指標');
  defs.forEach(function (d) {
    var chip = h('button', 'trend-chip' + (on[d.key] ? ' is-on' : ''));
    chip.type = 'button';
    chip.setAttribute('aria-pressed', on[d.key] ? 'true' : 'false');
    var sw = h('span', 'trend-chip-swatch' + (d.kind === 'bar-zero' ? ' is-bar-zero' : ''));
    if (d.color && d.kind !== 'bar-zero') sw.style.background = d.color;
    chip.appendChild(sw);
    chip.appendChild(h('span', null, d.label));
    chip.addEventListener('click', function () {
      if (on[d.key]) delete on[d.key]; else on[d.key] = true;
      saveToggle(section, defs.map(function (x) { return x.key; }).filter(function (k) { return on[k]; }));
      rerender();
    });
    row.appendChild(chip);
  });
  return row;
}

/** 開關型選項（例如「標出訓練日」）：與指標開關同一列，靠右。 */
function optionChip(label, key, fallback, rerender) {
  var value = flag(key, fallback);
  var chip = h('button', 'trend-chip trend-chip-option' + (value ? ' is-on' : ''));
  chip.type = 'button';
  chip.setAttribute('aria-pressed', value ? 'true' : 'false');
  chip.appendChild(h('span', 'trend-chip-check', value ? '✓' : ''));
  chip.appendChild(h('span', null, label));
  chip.addEventListener('click', function () {
    saveToggle(key, !value);
    rerender();
  });
  return chip;
}

/**
 * 大圖：把開著的 defs 交給 multiLineChart。def.points() 回傳 [{date, value}]。
 * 沒有任何開著的指標、或開著的都沒資料時顯示原因。
 */
function bigChart(node, label, defs, on, opts) {
  var shown = defs.filter(function (d) { return on[d.key]; });
  if (!shown.length) {
    showUnavailable(node, '請在上方開啟至少一個指標');
    return;
  }
  var series = shown.map(function (d) {
    return {
      key: d.key, label: d.label, color: d.color, unit: d.unit, kind: d.kind,
      formatter: d.formatter, decimals: d.decimals, invert: d.invert,
      points: d.points()
    };
  });
  if (!series.some(function (s) { return s.points.some(function (p) { return isNum(p.value); }); })) {
    showUnavailable(node, '此區間內開啟的指標都沒有資料');
    return;
  }
  node.appendChild(wrapChart(C.multiLineChart({
    series: series,
    label: label,
    containerWidth: widthOf(node),
    height: 260,
    startDate: opts && opts.startDate || undefined,
    endDate: opts && opts.endDate || undefined,
    highlightDates: opts && opts.highlightDates
  })));
}

/** 開關列＋大圖＋說明列的共用骨架：開關變動時只重畫這一塊。 */
function chartBlock(host, section, label, getDefs, opts) {
  function paint() {
    clear(host);
    var defs = getDefs();
    var on = onKeys(section, defs);
    var head = h('div', 'trend-chips-row');
    head.appendChild(chipRow(section, label, defs, on, paint));
    if (opts.option) head.appendChild(optionChip(opts.option.label, opts.option.key, opts.option.fallback, paint));
    host.appendChild(head);
    var chart = h('div', 'trend-chart');
    host.appendChild(chart);
    if (opts.ready && !opts.ready()) { showLoading(chart, '載入' + label); return; }
    bigChart(chart, label + '多指標折線圖', defs, on, opts.chartOpts ? opts.chartOpts() : null);
    var notes = opts.notes ? opts.notes(on) : [];
    notes.forEach(function (n) { host.appendChild(n instanceof Node ? n : h('div', 'chart-note', n)); });
  }
  paint();
}

// ------------------------------------------------------------ 1. 訓練負荷

function loadRows() { return (state.loadSeries && state.loadSeries.series) || []; }

function wellnessDef(key) {
  return wellnessMetricDefs().filter(function (d) { return d.key === key; })[0] || null;
}

/** 每日身體指標 → 大圖的系列定義。 */
function wellnessSeriesDef(def, color, extra) {
  var d = {
    key: def.key,
    label: def.label,
    color: color || 'var(--c-metric-' + def.key + ')',
    unit: def.format === 'duration' ? 'h' : def.unit,
    decimals: def.format === 'duration' ? 1 : def.decimals,
    on: false,
    points: function () { return wellnessPoints(state.wellness, def); }
  };
  Object.keys(extra || {}).forEach(function (k) { d[k] = extra[k]; });
  return d;
}

function loadDefs() {
  var defs = [
    { key: 'ctl', label: '長期體能 CTL', color: 'var(--c-pace)', unit: 'TRIMP', decimals: 1, on: true,
      points: function () { return loadSeriesToChartSeries(loadRows(), 'ctl'); } },
    { key: 'atl', label: '近期疲勞 ATL', color: 'var(--cat-load-strong)', unit: 'TRIMP', decimals: 1, on: true,
      points: function () { return loadSeriesToChartSeries(loadRows(), 'atl'); } },
    { key: 'tsb', label: '狀態平衡 TSB', kind: 'bar-zero', unit: 'TRIMP', decimals: 1, on: true,
      points: function () { return loadSeriesToChartSeries(loadRows(), 'tsb'); } },
    { key: 'garmin_acute', label: 'Garmin 急性負荷', color: 'var(--c-distance)', unit: 'Garmin 負荷', decimals: 0,
      points: function () {
        var ref = state.loadSeries && state.loadSeries.garmin_reference;
        return ((ref && ref.series) || []).map(function (g) { return { date: g.date, value: g.acute_load }; });
      } }
  ];
  LOAD_WELLNESS_KEYS.forEach(function (key) {
    var def = wellnessDef(key);
    if (def) defs.push(wellnessSeriesDef(def, null, key === 'acwr' ? { label: 'Garmin 負荷比', unit: '指數' } : null));
  });
  return defs;
}

/** 跑力說明的參考值：此區間最近一場，與截至區間結束的歷年範圍。 */
function vdotRefLines(lastVd, all) {
  var lines = [lastVd
    ? '此區間最近一場 ' + C.formatNumber(lastVd.vdot, 1) + '（依 ' + C.shortDate(lastVd.date) + ' ' + lastVd.distance + '）'
    : '此區間內無比賽成績'];
  if (all.length > 1) {
    lines.push('歷年比賽 ' + C.formatNumber(Math.min.apply(null, all), 1) + ' ～ '
      + C.formatNumber(Math.max.apply(null, all), 1) + '（共 ' + all.length + ' 場）');
  }
  return lines;
}

/** 恢復時間的區間統計：平均（含已恢復的日子）與已恢復天數，補足「期末值」看不到的整段情形。 */
function recoveryRangeLines(vals) {
  var nums = vals.filter(isNum);
  if (!nums.length) return '此區間沒有資料';
  var recovered = nums.filter(function (v) { return v < 1; }).length;
  return ['區間平均 ' + C.formatNumber(mean(nums), 1) + ' hr（含已恢復的日子）',
    '已恢復（< 1 hr）' + recovered + ' 天／共 ' + nums.length + ' 天'];
}

function lastFact(rows, key) {
  for (var i = rows.length - 1; i >= 0; i -= 1) if (rows[i][key] && isNum(rows[i][key].value)) return rows[i][key];
  return null;
}

function renderLoadSection(body) {
  var rows = loadRows();
  var fmt1 = function (v) { return C.formatNumber(v, 1); };
  var signed = function (v) { return (v > 0 ? '+' : '') + C.formatNumber(v, 1); };
  function prevLast(key) {
    return prevValue('loadSeries', function (d) {
      var f = lastFact(d.series || [], key);
      return f ? f.value : null;
    });
  }
  var recDef = wellnessDef('recovery_time_hours');
  var recPts = recDef ? wellnessPoints(state.wellness, recDef) : [];
  var tsbFact = lastFact(rows, 'tsb');
  var atlFact = lastFact(rows, 'atl');
  var ctlFact = lastFact(rows, 'ctl');
  body.appendChild(kpiRow([
    trendKpi({ label: '狀態平衡', abbr: 'TSB', value: tsbFact && tsbFact.value, fmt: signed, deltaFmt: fmt1,
      prev: prevLast('tsb'), note: '期末值',
      series: rows.map(function (r) { return r.tsb ? r.tsb.value : null; }),
      tip: loadTip('tsb', rows, '此區間').concat(confidenceTip(tsbFact)) }),
    trendKpi({ label: '近期疲勞', abbr: 'ATL', value: atlFact && atlFact.value, fmt: fmt1, prev: prevLast('atl'),
      note: '期末值', series: rows.map(function (r) { return r.atl ? r.atl.value : null; }),
      tip: loadTip('atl', rows, '此區間').concat(confidenceTip(atlFact)) }),
    trendKpi({ label: '長期體能', abbr: 'CTL', value: ctlFact && ctlFact.value, fmt: fmt1, prev: prevLast('ctl'),
      note: '期末值', series: rows.map(function (r) { return r.ctl ? r.ctl.value : null; }),
      tip: loadTip('ctl', rows, '此區間').concat(confidenceTip(ctlFact)) }),
    // 期末值：與同一排的 TSB／ATL／CTL、訓練完備程度卡的恢復時間同口徑（都是最新一天）。
    // 原本顯示區間平均，Garmin 恢復日回傳約 0，平均 12.9 hr 與卡片的「0」像是兩個矛盾的數字。
    trendKpi({ label: '恢復時間', value: lastValue(recPts), fmt: recoveryHoursText, unit: 'hr',
      prev: recDef ? prevValue('wellness', function (d) { return lastValue(wellnessPoints(d, recDef)); }) : null,
      note: '期末值', series: values(recPts),
      tip: [
        tipSection('定義', 'Garmin 估計還要幾小時才完全恢復，每天一筆（早上的快照）。0 表示已恢復，不到 1 小時顯示「< 1」。'),
        tipSection('區間統計', recoveryRangeLines(values(recPts)))
      ] })
  ]));

  var host = h('div', 'trend-chart-block');
  body.appendChild(host);
  chartBlock(host, 'load', '訓練負荷', loadDefs, {
    ready: function () { return !!state.loadSeries; },
    chartOpts: function () {
      var w = state.loadSeries || {};
      return { startDate: w.start_date, endDate: w.end_date };
    },
    notes: function (on) {
      var out = [];
      var all = state.loadSeries && !state.loadSeries.start_date;
      if (all) out.push('選「全部」時不提供 CTL／ATL／TSB（後端只計算有起日的區間），可改選其他區間。');
      if (on.tsb && tsbFact) {
        var level = (tsbFact.confidence && tsbFact.confidence.level) || 'high';
        var note = h('div', 'chart-note');
        note.appendChild(document.createTextNode('TSB：' + tsbFact.source + '，綠柱為正、紅柱為負　'));
        note.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
        out.push(note);
      }
      if (on.garmin_acute) {
        var ref = state.loadSeries && state.loadSeries.garmin_reference;
        var text = garminLoadNote(ref);
        if (text) out.push(text);
        if (ref && ref.note) out.push(ref.note);
      }
      return out;
    }
  });
}

// ------------------------------------------------------------ 2. 恢復與身體

function bodyDefs() {
  return wellnessMetricDefs().filter(function (d) { return LOAD_WELLNESS_KEYS.indexOf(d.key) === -1; })
    .map(function (def) { return wellnessSeriesDef(def, null, { on: BODY_DEFAULT_ON.indexOf(def.key) !== -1 }); });
}

function renderBodySection(body) {
  var cards = [
    { key: 'hrv_ms', label: 'HRV 夜間均值' },
    { key: 'resting_hr_bpm', label: '靜止心率' },
    { key: 'sleep_score', label: '睡眠分數' },
    { key: 'all_day_stress_avg', label: '壓力（全天）' }
  ].map(function (c) {
    var def = wellnessDef(c.key);
    if (!def) return trendKpi({ label: c.label, value: null, fmt: String, note: '後端未提供此指標' });
    var pts = wellnessPoints(state.wellness, def);
    var fmt = wellnessFormatter(def);
    return trendKpi({
      label: c.label, value: mean(values(pts)), fmt: fmt, unit: def.unit,
      deltaFmt: function (v) { return C.formatNumber(v, Math.max(def.decimals, 1)); },
      prev: prevValue('wellness', function (d) { return mean(values(wellnessPoints(d, def))); }),
      note: '期間平均', series: values(pts)
    });
  });
  body.appendChild(kpiRow(cards));

  var host = h('div', 'trend-chart-block');
  body.appendChild(host);
  chartBlock(host, 'body', '恢復與身體', bodyDefs, {
    option: { label: '標出訓練日', key: 'body:highlight', fallback: false },
    ready: function () { return !!state.wellness; },
    chartOpts: function () {
      var w = state.wellness || {};
      return {
        startDate: w.start_date, endDate: w.end_date,
        highlightDates: flag('body:highlight', false) ? state.trainingDays : null
      };
    },
    notes: function (on) {
      var out = [];
      if (flag('body:highlight', false)) out.push('淡色直條為有訓練的日子。');
      bodyDefs().forEach(function (d) {
        if (!on[d.key]) return;
        var clip = wellnessClipNote(state.wellness, wellnessDef(d.key));
        if (clip) out.push(clip);
      });
      return out;
    }
  });
}

// ------------------------------------------------------------ 3. 跑力表現

function sessionList() { return state.sessionsReady ? state.sessions : []; }

/** 時間加權的平均配速（總時間 ÷ 總距離）。 */
function avgPace(sessions) {
  var sec = 0;
  var km = 0;
  sessions.forEach(function (s) {
    if (isNum(s.duration_sec) && isNum(s.distance_km) && s.distance_km > 0 && isNum(s.avg_pace_sec_per_km)) {
      sec += s.duration_sec; km += s.distance_km;
    }
  });
  return km > 0 ? sec / km : null;
}

/** 時間加權的平均心率。 */
function avgHr(sessions) {
  var sum = 0;
  var sec = 0;
  sessions.forEach(function (s) {
    if (isNum(s.avg_hr_bpm) && isNum(s.duration_sec)) { sum += s.avg_hr_bpm * s.duration_sec; sec += s.duration_sec; }
  });
  return sec > 0 ? sum / sec : null;
}

/** /api/overview 的 vdot.history（歷年比賽，由舊到新）落在 [start, end] 的部分。 */
function vdotInWindow(start, end) {
  var hist = (state.overview && state.overview.vdot && state.overview.vdot.history) || [];
  return hist.filter(function (x) { return (!start || x.date >= start) && (!end || x.date <= end) && isNum(x.vdot); });
}

function abilityDefs() {
  function sessionDef(key, color, extra) {
    var spec = SESSION_TRENDS[key];
    var d = { key: key, label: spec.label, color: color, unit: spec.unit, formatter: spec.formatter,
      invert: spec.invert, on: false,
      points: function () { return seriesFromSessions(spec.field, sessionList()); } };
    Object.keys(extra || {}).forEach(function (k) { d[k] = extra[k]; });
    return d;
  }
  var win = currentWindow() || {};
  return [
    sessionDef('pace', 'var(--c-pace)', { on: true }),
    sessionDef('avg_hr', 'var(--c-hr)', { on: true }),
    sessionDef('te', 'var(--c-te)'),
    { key: 'vdot', label: 'VDOT（比賽）', color: 'var(--cat-ability-strong)', unit: 'VDOT', decimals: 1, on: false,
      points: function () {
        return vdotInWindow(win.start, win.end).map(function (x) { return { date: x.date, value: x.vdot }; });
      } }
  ];
}

function renderAbilitySection(body) {
  var sessions = sessionList();
  var win = currentWindow() || {};
  var p = state.trendsPrev;
  var vd = vdotInWindow(win.start, win.end);
  var lastVd = vd.length ? vd[vd.length - 1] : null;
  var prevVd;
  if (!p) prevVd = null;
  else if (!state.overview) prevVd = undefined;
  else {
    var pv = vdotInWindow(p.start, p.end);
    prevVd = pv.length ? pv[pv.length - 1].vdot : null;
  }
  var fmt1 = function (v) { return C.formatNumber(v, 1); };
  var fmt0 = function (v) { return C.formatNumber(v, 0); };
  function prevSessions(fn) { return prevValue('sessions', fn); }
  body.appendChild(kpiRow([
    trendKpi({ label: '跑力', abbr: 'VDOT', value: lastVd ? lastVd.vdot : null, fmt: fmt1, prev: prevVd,
      note: lastVd ? '依 ' + C.shortDate(lastVd.date) + ' ' + lastVd.distance : '此區間內無比賽成績',
      series: vdotInWindow(null, win.end).map(function (x) { return x.vdot; }),
      tip: [
        tipSection('參考值', vdotRefLines(lastVd, vdotInWindow(null, win.end).map(function (x) { return x.vdot; }))),
        tipSection('定義', '區間內最近一場比賽（或全力測驗）成績，以 Jack Daniels 公式換算的跑力指數。'),
        tipSection('走勢線', '截至區間結束的歷年每場比賽各自算出的 VDOT。')
      ] }),
    trendKpi({ label: '平均配速', value: avgPace(sessions), fmt: C.formatPace, unit: '/km', prev: prevSessions(avgPace),
      note: '總時間 ÷ 總距離', invert: true,
      series: values(seriesFromSessions('avg_pace_sec_per_km', sessions)) }),
    trendKpi({ label: '每場平均心率', value: avgHr(sessions), fmt: fmt0, unit: 'bpm', prev: prevSessions(avgHr),
      note: '依時間加權', series: values(seriesFromSessions('avg_hr_bpm', sessions)) }),
    trendKpi({ label: '有氧 TE', value: mean(sessions.map(function (s) { return s.aerobic_te; })), fmt: fmt1,
      prev: prevSessions(function (list) { return mean(list.map(function (s) { return s.aerobic_te; })); }),
      note: '每場平均', series: values(seriesFromSessions('aerobic_te', sessions)) })
  ]));

  var host = h('div', 'trend-chart-block');
  body.appendChild(host);
  chartBlock(host, 'ability', '跑力表現', abilityDefs, {
    ready: function () { return state.sessionsReady; },
    chartOpts: function () { var w = currentWindow() || {}; return { startDate: w.start, endDate: w.end }; },
    notes: function (on) {
      var out = ['每場一點（跑步場次），點與點之間直接相連。'];
      if (on.pace) out.push('配速軸上快下慢。');
      return out;
    }
  });
}

// ------------------------------------------------------------ 4. 訓練量與強度

var VOLUME_DEFS = [
  { key: 'weekly', label: '每週跑量（依強度堆疊）', color: 'var(--c-volume)', on: true },
  { key: 'distance', label: '每場距離', color: 'var(--c-distance)' },
  { key: 'count', label: '每週次數', color: 'var(--c-te)' }
];

function lowShare(zones) {
  if (!zones || !zones.available) return null;
  var pct = {};
  zones.zones.forEach(function (z) { pct[z.zone] = z.pct || 0; });
  return (pct[1] || 0) + (pct[2] || 0);
}

function weeklyCounts(weeks, sessions) {
  var count = {};
  sessions.forEach(function (s) { var m = mondayOf(s.date); count[m] = (count[m] || 0) + 1; });
  return weeks.map(function (w) { return count[w.week_start] || 0; });
}

function maxDistance(sessions) {
  var vals = sessions.map(function (s) { return s.distance_km; }).filter(isNum);
  return vals.length ? Math.max.apply(null, vals) : null;
}

function volumeCharts(host) {
  function paint() {
    clear(host);
    var on = onKeys('volume', VOLUME_DEFS);
    host.appendChild(h('div', 'trend-chips-row')).appendChild(chipRow('volume', '訓練量', VOLUME_DEFS, on, paint));
    var any = false;
    if (on.weekly) {
      any = true;
      var wk = h('div', 'trend-chart trend-subchart');
      host.appendChild(wk);
      renderWeeklyVolume(wk);
    }
    if (on.distance) {
      any = true;
      var dist = h('div', 'trend-chart trend-subchart');
      host.appendChild(dist);
      dist.appendChild(h('h3', 'chart-title', '每場距離（km）'));
      if (!state.sessionsReady) showLoading(dist, '載入場次');
      else {
        var w = currentWindow() || {};
        bigChart(dist, '每場距離折線圖', [{
          key: 'distance', label: '每場距離', color: 'var(--c-distance)', unit: 'km', decimals: 1,
          points: function () { return seriesFromSessions('distance_km', sessionList()); }
        }], { distance: true }, { startDate: w.start, endDate: w.end });
      }
    }
    if (on.count) {
      any = true;
      var cnt = h('div', 'trend-chart trend-subchart');
      host.appendChild(cnt);
      cnt.appendChild(h('h3', 'chart-title', '每週次數（跑步場次，週一起算）'));
      var ov = state.overview;
      if (!ov || !state.sessionsReady) showLoading(cnt, '載入場次');
      else if (!ov.available || !ov.weekly_intensity.weeks.length) showUnavailable(cnt, (ov && ov.reason) || '此區間內沒有跑步');
      else {
        var weeks = ov.weekly_intensity.weeks;
        var counts = weeklyCounts(weeks, sessionList());
        cnt.appendChild(wrapChart(C.barChart(weeks.map(function (wk2, i) {
          return { label: C.shortDate(wk2.week_start), weekStart: wk2.week_start, value: counts[i],
            partial: wk2.partial, tipSuffix: wk2.partial ? '（不完整週）' : '' };
        }), {
          label: '每週跑步次數長條圖',
          formatter: function (v) { return C.formatNumber(v, 0); },
          barClass: 'bar-volume',
          containerWidth: widthOf(cnt)
        })));
      }
    }
    if (!any) host.appendChild(h('div', 'state-msg state-unavailable', '請在上方開啟至少一個指標'));
  }
  paint();
}

function renderVolumeSection(body) {
  var ov = state.overview && state.overview.available ? state.overview : null;
  var sessions = sessionList();
  var weeks = ov ? ov.weekly_intensity.weeks : [];
  var fmt1 = function (v) { return C.formatNumber(v, 1); };
  var fmt0 = function (v) { return C.formatNumber(v, 0); };
  function prevOv(fn) {
    return prevValue('overview', function (d) { return d.available ? fn(d) : null; });
  }
  body.appendChild(kpiRow([
    trendKpi({ label: '本期跑量', value: ov ? ov.sessions.total_km : null, fmt: fmt1, unit: 'km',
      prev: prevOv(function (d) { return d.sessions.total_km; }), note: '區間合計',
      series: weeks.map(function (w) { return w.total_km; }) }),
    trendKpi({ label: '次數', value: ov ? ov.sessions.total : null, fmt: fmt0, unit: '場',
      prev: prevOv(function (d) { return d.sessions.total; }), note: '跑步場次',
      series: weeklyCounts(weeks, sessions) }),
    trendKpi({ label: 'Z1–Z2 佔比', value: ov ? lowShare(ov.hr_zones) : null, fmt: fmt1, unit: '%',
      prev: prevOv(function (d) { return lowShare(d.hr_zones); }), note: '心率區間停留時間',
      series: weeks.map(function (w) {
        var known = w.total_km - w.unknown_km;
        return known > 0 ? w.low_km / known * 100 : null;
      }),
      tip: ['Z1 與 Z2 的停留時間佔 Z1～Z5 總時間的比例（不含低於 Z1）。走勢線是每週低強度距離佔有心率區間距離的比例。'] }),
    trendKpi({ label: '最長一次', value: maxDistance(sessions), fmt: fmt1, unit: 'km',
      prev: prevValue('sessions', maxDistance), note: '單場距離',
      series: values(seriesFromSessions('distance_km', sessions)) })
  ]));

  var grid = h('div', 'trend-volume-grid');
  var left = h('div', 'trend-volume-col');
  var zones = h('div', 'trend-card trend-zones');
  left.appendChild(zones);
  var recent = h('div', 'trend-card trend-recent');
  left.appendChild(recent);
  var right = h('div', 'trend-volume-col trend-chart-block');
  grid.appendChild(left);
  grid.appendChild(right);
  body.appendChild(grid);
  renderZones(zones);
  renderRecentVolume(recent);
  volumeCharts(right);
}

// ------------------------------------------------------------ 5. Garmin 數據

function garminDefs() {
  return GARMIN_SERIES_DEFS.map(function (d) {
    return { key: d.key, label: d.label, color: d.color, unit: d.unit, decimals: d.decimals, on: !!d.on,
      points: function () { return garminSeriesPoints(state.garminCards, d); } };
  });
}

function garminCardDefs() {
  return GARMIN_CARD_DEFS.map(function (d) { return { key: d.key, label: d.label }; });
}

function renderGarminCards(host) {
  clear(host);
  var open = flag('garmin:cards', true);
  var head = h('div', 'trend-gc-head');
  var toggle = h('button', 'trend-chip trend-chip-option' + (open ? ' is-on' : ''));
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  toggle.appendChild(h('span', 'trend-chip-check', open ? '▾' : '▸'));
  toggle.appendChild(h('span', null, open ? 'Garmin 圖卡（點此收合）' : 'Garmin 圖卡（點此展開）'));
  toggle.addEventListener('click', function () {
    saveToggle('garmin:cards', !open);
    renderGarminCards(host);
  });
  head.appendChild(toggle);
  if (open) {
    head.appendChild(customizer({
      settingsKey: 'trends-garmin',
      defs: garminCardDefs(),
      label: '⚙ 自訂圖卡',
      onChange: function () { renderGarminCards(host); }
    }));
  }
  host.appendChild(head);
  if (!open) return;
  var grid = h('div', 'gc-grid');
  visibleDefs('trends-garmin', garminCardDefs()).forEach(function (d) {
    var slot = h('div', 'gc-slot');
    grid.appendChild(slot);
    if (!state.garminCards) showLoading(slot, '載入 ' + d.label);
    else renderGarminCard(d.key, state.garminCards.cards[d.key], slot);
  });
  host.appendChild(grid);
}

function renderGarminSection(body) {
  var host = h('div', 'trend-chart-block');
  body.appendChild(host);
  chartBlock(host, 'garmin', 'Garmin 數據', garminDefs, {
    ready: function () { return !!state.garminCards; },
    chartOpts: function () {
      var g = state.garminCards || {};
      return { startDate: g.start_date, endDate: g.end_date };
    },
    notes: function () { return ['同一張圖開到第三種單位時，每條線改用各自的刻度、只比形狀，數值看游標提示。']; }
  });

  var tiles = h('div', 'trend-gc-tiles');
  body.appendChild(tiles);
  if (!state.garminCards) showLoading(tiles, '載入 Garmin 數據');
  else tiles.appendChild(garminScoreTiles(state.garminCards));

  var cards = h('div', 'trend-gc-cards');
  body.appendChild(cards);
  renderGarminCards(cards);
}

// ------------------------------------------------------------ 組裝

var CATEGORIES = [
  { key: 'load', label: '訓練負荷', render: renderLoadSection },
  { key: 'body', label: '恢復與身體', render: renderBodySection },
  { key: 'ability', label: '跑力表現', render: renderAbilitySection },
  { key: 'volume', label: '訓練量與強度', render: renderVolumeSection },
  { key: 'garmin', label: 'Garmin 數據', hint: '手錶與 Garmin Connect 的輸出，僅供顯示，不納入本專案判斷',
    render: renderGarminSection }
];

function compareHint() {
  var p = state.trendsPrev;
  if (p) return '數字卡的 ▲▼：與前一段等長期間（' + spanText(p.start, p.end) + '）比較，只標增減';
  var win = currentWindow();
  if (win && !win.start) return '選「全部」時沒有前一段期間，數字卡不顯示 ▲▼';
  return '';
}

export function renderTrendsPage() {
  var body = $('trends-body');
  if (!body) return;
  closeInfoPopover(false); // 重畫會換掉 ⓘ 按鈕，開著的說明要先關
  ensurePrevious();
  renderOverviewStrip();

  var head = $('trends-customize');
  clear(head);
  head.appendChild(h('p', 'trend-compare-hint', compareHint()));
  head.appendChild(customizer({
    settingsKey: 'trends',
    defs: CATEGORIES,
    label: '⚙ 自訂類別',
    hint: '拖移 ⠿ 調整類別順序，取消勾選可隱藏整個類別。各類別要畫哪些指標，在類別內的開關列切換。',
    onChange: renderTrendsPage
  }));

  clear(body);
  // 先把所有區塊放進頁面，再逐區畫：圖表寬度依容器實際寬度決定，必須已在 DOM 中
  var jobs = [];
  visibleDefs('trends', CATEGORIES).forEach(function (cat) {
    var section = h('section', 'panel trend-category trend-sec trend-sec-' + cat.key);
    section.id = 'trend-cat-' + cat.key;
    var title = h('h2', 'panel-title', cat.label);
    if (cat.hint) title.appendChild(h('span', 'panel-hint', cat.hint));
    section.appendChild(title);
    var inner = h('div', 'trend-sec-inner');
    section.appendChild(inner);
    body.appendChild(section);
    jobs.push({ cat: cat, node: inner });
  });
  jobs.forEach(function (job) { job.cat.render(job.node); });
}

/** 換時間範圍時：清掉舊範圍的資料並顯示載入中，避免新舊資料混在同一頁。 */
export function showTrendsLoading() {
  state.loadSeries = null;
  state.wellness = null;
  state.garminCards = null;
  state.overview = null;
  state.sessionsReady = false;
  state.trendsPrev = null;
  prevRequestedFor = null;
  var body = $('trends-body');
  if (body && !body.childNodes.length) showLoading(body, '載入趨勢');
}
