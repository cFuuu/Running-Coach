/**
 * load.js — 趨勢頁的訓練負荷圖（TSB、Garmin 急性負荷）與跨場趨勢圖（每場的距離、配速、心率、TE）
 *
 * 每個 render 函式把一張圖（標題＋圖＋說明）畫進呼叫端給的容器；
 * 版面與類別由 views/trends.js 決定。
 */

import * as C from '../charts.js';
import { state, CONFIDENCE_LABEL, GARMIN_TRAINING_STATUS_LABEL, GARMIN_ACWR_STATUS_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { h, showError, widthOf, wrapChart } from '../core/dom.js';
import { renderOrDefer } from '../shell/router.js';
import { renderTrendsPage } from './trends.js';

/**
 * 把 /api/training-load 的 fact 序列（{value, source, confidence}）
 * 轉成 charts.trendLineChart() 要的簡單 {date, value} 序列。信賴度
 * badge 顯示在圖表下方的說明列（TSB 每天信賴度理論上相同，逐點畫
 * badge 會過度擁擠），不逐點畫在圖上——這是折線圖已有的既有限制
 * （trendLineChart 不支援逐點 badge），非本次新增的缺陷。
 */
export function loadSeriesToChartSeries(series, key) {
  return series.map(function (day) {
    var fact = day[key];
    return { date: day.date, value: fact ? fact.value : null };
  });
}

/** 訓練負荷資料還沒回來或沒有資料時，回傳 false 並在容器顯示原因。 */
function loadSeriesReady(node, title) {
  node.appendChild(h('h3', 'chart-title', title));
  if (!state.loadSeries) {
    node.appendChild(h('div', 'state-msg state-loading', '載入訓練負荷…'));
    return false;
  }
  if (state.loadSeries.series.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
    return false;
  }
  return true;
}

export function renderTsbChart(node) {
  if (!loadSeriesReady(node, 'TSB（訓練壓力平衡，CTL − ATL）')) return;
  var rows = state.loadSeries.series;
  var hasAny = rows.some(function (r) { return r.tsb && r.tsb.value !== null; });
  if (!hasAny) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
    return;
  }
  node.appendChild(wrapChart(C.trendLineChart(loadSeriesToChartSeries(rows, 'tsb'), {
    label: 'TSB',
    formatter: function (v) { return C.formatNumber(v, 1); },
    lineClass: 'line-te',
    dotClass: 'dot-te',
    containerWidth: widthOf(node)
  })));
  var firstConf = rows.map(function (r) { return r.tsb; }).filter(Boolean)[0];
  if (firstConf) {
    var level = (firstConf.confidence && firstConf.confidence.level) || 'high';
    var note = h('div', 'chart-note');
    note.appendChild(document.createTextNode(firstConf.source + '　'));
    note.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
    node.appendChild(note);
  }
}

/**
 * Garmin 急性負荷（僅供對照）。走 garmin_reference（for_display_only），
 * 不是 fact() 契約，故不掛信賴度 badge、改標「僅供對照」。X 軸沿用 TSB
 * 序列的日期，Garmin 沒資料的日子留空斷線，兩張圖才對得齊。
 */
export function renderGarminAcuteChart(node) {
  if (!loadSeriesReady(node, 'Garmin 急性負荷')) return;
  var ref = state.loadSeries.garmin_reference;
  var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
  refBadge.title = (ref && ref.note) || '';
  node.querySelector('.chart-title').appendChild(refBadge);
  if (!ref || !ref.series || ref.series.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有 Garmin 負荷資料'));
    return;
  }
  var rows = state.loadSeries.series;
  var byDate = {};
  ref.series.forEach(function (d) { byDate[d.date] = d; });
  var latest = ref.series[ref.series.length - 1];

  node.appendChild(wrapChart(C.trendLineChart(rows.map(function (r) {
    var g = byDate[r.date];
    return { date: r.date, value: g ? g.acute_load : null };
  }), {
    label: 'Garmin 急性負荷',
    formatter: function (v) { return C.formatNumber(v, 0); },
    containerWidth: widthOf(node)
  })));

  var statusKey = (latest.status || '').replace(/_\d+$/, '');
  var parts = [
    '截至 ' + C.shortDate(latest.date),
    '訓練狀態 ' + (GARMIN_TRAINING_STATUS_LABEL[statusKey] || latest.status || '—'),
    '急性 ' + C.formatNumber(latest.acute_load, 0) + ' / 慢性 ' + C.formatNumber(latest.chronic_load, 0)
  ];
  if (latest.acute_load_optimal_min !== null && latest.acute_load_optimal_max !== null) {
    parts.push('建議急性範圍 ' + C.formatNumber(latest.acute_load_optimal_min, 0) + '–'
      + C.formatNumber(latest.acute_load_optimal_max, 0));
  }
  if (latest.acwr !== null) {
    parts.push('負荷比 ' + C.formatNumber(latest.acwr, 1)
      + (latest.acwr_status ? '（' + (GARMIN_ACWR_STATUS_LABEL[latest.acwr_status] || latest.acwr_status) + '）' : ''));
  }
  node.appendChild(h('div', 'chart-note', parts.join('，')));
  node.appendChild(h('div', 'chart-note', ref.note));
}

export function loadLoadSeries() {
  api('/api/training-load?range=' + state.range).then(function (data) {
    state.loadSeries = data;
    renderOrDefer('trends', renderTrendsPage);
  }).catch(function (err) {
    var node = document.getElementById('trend-cat-load');
    if (node) showError(node.querySelector('.trend-grid') || node, err.message);
  });
}

// ------------------------------------------------------------ 跨場趨勢（每場一點）

/** 以場次日期建立趨勢資料（由舊到新） */
export function seriesFromSessions(field) {
  return state.sessions.slice().reverse().map(function (s) {
    var v = s[field];
    return { date: s.date, value: (typeof v === 'number' && isFinite(v)) ? v : null };
  });
}

// 每場一點的趨勢圖，trends.js 依 key 歸類到各類別
export var SESSION_TRENDS = {
  distance: { title: '距離（每場，km）', field: 'distance_km', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-distance', dotClass: 'dot-distance' },
  pace: { title: '平均配速（每場，/km，上快下慢）', field: 'avg_pace_sec_per_km', formatter: C.formatPace, invertY: true, lineClass: 'line-pace', dotClass: 'dot-pace' },
  avg_hr: { title: '平均心率（每場，bpm）', field: 'avg_hr_bpm', formatter: function (v) { return C.formatNumber(v, 0); }, lineClass: 'line-hr', dotClass: 'dot-hr' },
  te: { title: '有氧訓練效果（每場，TE）', field: 'aerobic_te', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-te', dotClass: 'dot-te' }
};

export function renderSessionTrend(spec, node) {
  node.appendChild(h('h3', 'chart-title', spec.title));
  if (!state.sessionsReady) {
    node.appendChild(h('div', 'state-msg state-loading', '載入場次…'));
    return;
  }
  var series = seriesFromSessions(spec.field);
  if (!series.some(function (p) { return p.value !== null; })) {
    node.appendChild(h('div', 'state-msg state-unavailable',
      state.sessions.length === 0 ? '此區間內沒有跑步場次' : '此區間內此指標無資料'));
    return;
  }
  node.appendChild(wrapChart(C.trendLineChart(series, {
    label: spec.title,
    formatter: spec.formatter,
    invertY: spec.invertY,
    lineClass: spec.lineClass,
    dotClass: spec.dotClass,
    containerWidth: widthOf(node)
  })));
}
