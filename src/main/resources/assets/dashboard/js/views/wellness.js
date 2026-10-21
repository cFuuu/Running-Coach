/**
 * wellness.js — 身體狀況：每日指標折線（排序／顯隱見 core/customize.js）
 */

import * as C from '../charts.js';
import { state, wellnessMetricDefs } from '../core/state.js';
import { $, clear, h, widthOf, wrapChart } from '../core/dom.js';
import { customizer, visibleDefs } from '../core/customize.js';

/** 依 def.format 選對應的格式化函式（'duration' 用時分秒，其餘用一般數字）。 */
export function wellnessFormatter(def) {
  if (def.format === 'duration') return function (v) { return C.formatDuration(v); };
  return function (v) { return C.formatNumber(v, def.decimals); };
}

export function renderWellness() {
  var node = $('wellness-charts');
  clear(node);
  if (!state.wellness) return;

  // 指標的排序與顯隱（偏好 key 沿用 'wellness'，使用者原本的設定不會遺失）
  node.appendChild(customizer({
    settingsKey: 'wellness',
    defs: wellnessMetricDefs(),
    onChange: renderWellness
  }));

  var metrics = state.wellness.metrics || {};
  var startDate = state.wellness.start_date;
  var endDate = state.wellness.end_date;

  visibleDefs('wellness', wellnessMetricDefs()).forEach(function (def) {
    var m = metrics[def.key];
    var formatter = wellnessFormatter(def);
    var card = h('div', 'chart-block');
    var titleRow = h('div', 'chart-title-row');
    titleRow.appendChild(h('h3', 'chart-title', def.label + (def.unit ? '（' + def.unit + '）' : '')));

    if (!m) {
      card.appendChild(titleRow);
      card.appendChild(h('div', 'state-msg state-unavailable', '後端未提供此指標'));
      node.appendChild(card);
      return;
    }

    // clipped:true → 明確提示，且絕不把缺的部分畫成 0 或平線
    if (m.clipped) {
      var cov = m.coverage && m.coverage.earliest_date ? m.coverage.earliest_date : null;
      titleRow.appendChild(h('span', 'badge badge-clipped',
        cov ? (cov + ' 前無資料') : '此日期前無資料'));
    }
    card.appendChild(titleRow);

    var points = (m.points || []).filter(function (p) {
      return typeof p.value === 'number' && isFinite(p.value);
    });

    if (m.available === false || points.length === 0) {
      card.appendChild(h('div', 'state-msg state-unavailable',
        m.reason || '此區間內無資料'));
      node.appendChild(card);
      return;
    }

    // 圖表 X 軸起點：clipped 時從實際有資料的第一天畫起，避免產生誤導的空白平線
    var chartStart = startDate;
    if (m.clipped && m.coverage && m.coverage.earliest_date && m.coverage.earliest_date > startDate) {
      chartStart = m.coverage.earliest_date;
    }
    if (points[0].date > chartStart) chartStart = points[0].date;

    card.appendChild(wrapChart(C.wellnessChart(points, {
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
    card.appendChild(h('div', 'chart-note',
      '最新（' + latest.date + '）：' + formatter(latest.value)
      + (def.unit && def.format !== 'duration' ? ' ' + def.unit : '') + '　・　共 ' + points.length + ' 天有資料'));
    node.appendChild(card);
  });
}
