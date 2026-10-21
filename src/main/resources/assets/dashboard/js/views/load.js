/**
 * load.js — 訓練負荷 view：ATL／CTL／TSB 折線、Garmin 負荷對照、跨場趨勢
 */

import * as C from '../charts.js';
import { state, CONFIDENCE_LABEL, GARMIN_TRAINING_STATUS_LABEL, GARMIN_ACWR_STATUS_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError, widthOf, wrapChart } from '../core/dom.js';
import { renderOrDefer } from '../shell/router.js';

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

export function renderLoad() {
  var node = $('load-chart');
  clear(node);
  var series = state.loadSeries;
  if (!series || series.series.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
    return;
  }
  var rows = series.series;
  var hasAny = rows.some(function (r) { return r.tsb && r.tsb.value !== null; });
  var card = h('div', 'chart-block');
  card.appendChild(h('h3', 'chart-title', 'TSB（訓練壓力平衡，CTL − ATL）'));
  if (!hasAny) {
    card.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
  } else {
    card.appendChild(wrapChart(C.trendLineChart(loadSeriesToChartSeries(rows, 'tsb'), {
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
      card.appendChild(note);
    }
  }
  node.appendChild(card);
  var garminBlock = garminLoadBlock(series.garmin_reference, rows, widthOf(node));
  if (garminBlock) node.appendChild(garminBlock);
}

/**
 * Garmin 急性負荷（僅供對照）。走 garmin_reference（for_display_only），
 * 不是 fact() 契約，故不掛信賴度 badge、改標「僅供對照」。X 軸沿用 TSB
 * 序列的日期，Garmin 沒資料的日子留空斷線，兩張圖才對得齊。
 */
export function garminLoadBlock(ref, rows, containerWidth) {
  if (!ref || !ref.series || ref.series.length === 0) return null;
  var byDate = {};
  ref.series.forEach(function (d) { byDate[d.date] = d; });
  var latest = ref.series[ref.series.length - 1];

  var card = h('div', 'chart-block');
  var title = h('h3', 'chart-title', 'Garmin 急性負荷');
  var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
  refBadge.title = ref.note || '';
  title.appendChild(refBadge);
  card.appendChild(title);
  card.appendChild(wrapChart(C.trendLineChart(rows.map(function (r) {
    var g = byDate[r.date];
    return { date: r.date, value: g ? g.acute_load : null };
  }), {
    label: 'Garmin 急性負荷',
    formatter: function (v) { return C.formatNumber(v, 0); },
    containerWidth: containerWidth
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
  card.appendChild(h('div', 'chart-note', parts.join('　·　')));
  card.appendChild(h('div', 'chart-note', ref.note));
  return card;
}

export function loadLoadSeries() {
  showLoading($('load-chart'), '載入訓練負荷');
  api('/api/training-load?range=' + state.range).then(function (data) {
    state.loadSeries = data;
    renderOrDefer('load', renderLoad);
  }).catch(function (err) {
    showError($('load-chart'), err.message);
  });
}

// ------------------------------------------------------------ 區塊四之二：跨場趨勢

/** 以場次日期建立趨勢資料（由舊到新） */
export function seriesFromSessions(field) {
  return state.sessions.slice().reverse().map(function (s) {
    var v = s[field];
    return { date: s.date, value: (typeof v === 'number' && isFinite(v)) ? v : null };
  });
}

/** 計算週跑量（以 ISO 週一為一週起點） */
export function weeklyVolume() {
  var buckets = {};
  state.sessions.forEach(function (s) {
    if (typeof s.distance_km !== 'number' || !isFinite(s.distance_km)) return;
    var d = new Date(s.date + 'T00:00:00Z');
    var dow = d.getUTCDay();              // 0=週日
    var offset = dow === 0 ? 6 : dow - 1; // 回推到該週週一
    d.setUTCDate(d.getUTCDate() - offset);
    var key = d.toISOString().slice(0, 10);
    buckets[key] = (buckets[key] || 0) + s.distance_km;
  });
  return Object.keys(buckets).sort().map(function (k) {
    return { label: C.shortDate(k), value: Math.round(buckets[k] * 10) / 10, weekStart: k };
  });
}

export function renderTrends() {
  var node = $('trend-charts');
  clear(node);
  if (state.sessions.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步場次'));
    return;
  }

  var specs = [
    { title: '距離（km）', field: 'distance_km', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-distance', dotClass: 'dot-distance' },
    { title: '平均配速（/km，上快下慢）', field: 'avg_pace_sec_per_km', formatter: C.formatPace, invertY: true, lineClass: 'line-pace', dotClass: 'dot-pace' },
    { title: '平均心率（bpm）', field: 'avg_hr_bpm', formatter: function (v) { return C.formatNumber(v, 0); }, lineClass: 'line-hr', dotClass: 'dot-hr' },
    { title: '有氧訓練效果（TE）', field: 'aerobic_te', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-te', dotClass: 'dot-te' }
  ];

  specs.forEach(function (spec) {
    var card = h('div', 'chart-block');
    card.appendChild(h('h3', 'chart-title', spec.title));
    var series = seriesFromSessions(spec.field);
    var hasValue = series.some(function (p) { return p.value !== null; });
    if (!hasValue) {
      card.appendChild(h('div', 'state-msg state-unavailable', '此區間內此指標無資料'));
    } else {
      card.appendChild(wrapChart(C.trendLineChart(series, {
        label: spec.title,
        formatter: spec.formatter,
        invertY: spec.invertY,
        lineClass: spec.lineClass,
        dotClass: spec.dotClass,
        containerWidth: widthOf(node)
      })));
    }
    node.appendChild(card);
  });

  var volumeCard = h('div', 'chart-block');
  volumeCard.appendChild(h('h3', 'chart-title', '週跑量（km）'));
  var bars = weeklyVolume();
  if (bars.length === 0) {
    volumeCard.appendChild(h('div', 'state-msg state-unavailable', '此區間內無距離資料'));
  } else {
    volumeCard.appendChild(wrapChart(C.barChart(bars, {
      label: '週跑量長條圖',
      formatter: function (v) { return C.formatNumber(v, 1); },
      barClass: 'bar-volume',
      containerWidth: widthOf(node)
    })));
  }
  node.appendChild(volumeCard);
}
