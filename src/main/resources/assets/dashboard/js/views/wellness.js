/**
 * wellness.js — 趨勢頁的每日身體指標折線（歸類與排序見 views/trends.js）
 */

import * as C from '../charts.js';
import { state } from '../core/state.js';
import { h, widthOf, wrapChart } from '../core/dom.js';

/** 依 def.format 選對應的格式化函式（'duration' 用時分秒，其餘用一般數字）。 */
export function wellnessFormatter(def) {
  if (def.format === 'duration') return function (v) { return C.formatDuration(v); };
  return function (v) { return C.formatNumber(v, def.decimals); };
}

/**
 * 一個每日指標的折線圖（標題＋圖＋最新值）畫進 node。版面與歸類由 views/trends.js 決定。
 * @param {Object} def /api/meta 的 wellness_metric_defs 其中一筆
 */
export function renderWellnessMetric(def, node) {
  var titleRow = h('div', 'chart-title-row');
  titleRow.appendChild(h('h3', 'chart-title', def.label + (def.unit ? '（' + def.unit + '）' : '')));
  node.appendChild(titleRow);
  if (!state.wellness) {
    node.appendChild(h('div', 'state-msg state-loading', '載入身體狀況…'));
    return;
  }
  var m = (state.wellness.metrics || {})[def.key];
  if (!m) {
    node.appendChild(h('div', 'state-msg state-unavailable', '後端未提供此指標'));
    return;
  }
  var startDate = state.wellness.start_date;
  var endDate = state.wellness.end_date;
  var formatter = wellnessFormatter(def);

  // clipped:true → 明確提示，且絕不把缺的部分畫成 0 或平線
  if (m.clipped) {
    var cov = m.coverage && m.coverage.earliest_date ? m.coverage.earliest_date : null;
    titleRow.appendChild(h('span', 'badge badge-clipped',
      cov ? (cov + ' 前無資料') : '此日期前無資料'));
  }

  var points = (m.points || []).filter(function (p) {
    return typeof p.value === 'number' && isFinite(p.value);
  });
  if (m.available === false || points.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', m.reason || '此區間內無資料'));
    return;
  }

  // 圖表 X 軸起點：clipped 時從實際有資料的第一天畫起，避免產生誤導的空白平線
  var chartStart = startDate;
  if (m.clipped && m.coverage && m.coverage.earliest_date && m.coverage.earliest_date > startDate) {
    chartStart = m.coverage.earliest_date;
  }
  if (points[0].date > chartStart) chartStart = points[0].date;

  node.appendChild(wrapChart(C.wellnessChart(points, {
    label: def.label + '折線圖',
    startDate: chartStart,
    endDate: endDate,
    trainingDays: state.trainingDays,
    formatter: formatter,
    lineClass: 'line-metric-' + def.key,
    dotClass: 'dot-metric',
    containerWidth: widthOf(node)
  })));

  var latest = points[points.length - 1];
  node.appendChild(h('div', 'chart-note',
    '最新（' + latest.date + '）：' + formatter(latest.value)
    + (def.unit && def.format !== 'duration' ? ' ' + def.unit : '') + '，共 ' + points.length + ' 天有資料'));
}
