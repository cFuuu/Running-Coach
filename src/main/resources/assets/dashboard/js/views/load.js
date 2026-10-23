/**
 * load.js — 趨勢頁的訓練負荷資料（/api/training-load）與跨場指標（每場的距離、配速、心率、TE）
 *
 * 只放載入與資料轉換；版面與畫圖由 views/trends.js 決定（E.2 起各區改用一張多指標大圖）。
 */

import * as C from '../charts.js';
import { state, GARMIN_TRAINING_STATUS_LABEL, GARMIN_ACWR_STATUS_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { showError } from '../core/dom.js';
import { renderOrDefer } from '../shell/router.js';
import { renderTrendsPage } from './trends.js';

/**
 * 把 /api/training-load 的 fact 序列（{value, source, confidence}）
 * 轉成圖表要的簡單 {date, value} 序列。信賴度 badge 顯示在圖表下方的
 * 說明列（TSB 每天信賴度理論上相同，逐點畫 badge 會過度擁擠）。
 */
export function loadSeriesToChartSeries(series, key) {
  return series.map(function (day) {
    var fact = day[key];
    return { date: day.date, value: fact ? fact.value : null };
  });
}

/**
 * Garmin 急性負荷（僅供對照）的最新狀態摘要文字：訓練狀態、急性／慢性、建議範圍、負荷比。
 * 走 garmin_reference（for_display_only），不是 fact() 契約。沒有資料回 null。
 */
export function garminLoadNote(ref) {
  if (!ref || !ref.series || ref.series.length === 0) return null;
  var latest = ref.series[ref.series.length - 1];
  var statusKey = (latest.status || '').replace(/_\d+$/, '');
  var parts = [
    'Garmin 截至 ' + C.shortDate(latest.date),
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
  return parts.join('，');
}

export function loadLoadSeries() {
  api('/api/training-load?range=' + state.range).then(function (data) {
    state.loadSeries = data;
    renderOrDefer('trends', renderTrendsPage);
  }).catch(function (err) {
    var node = document.getElementById('trend-cat-load');
    if (node) showError(node.querySelector('.trend-chart') || node, err.message);
  });
}

// ------------------------------------------------------------ 跨場趨勢（每場一點）

/**
 * 以場次日期建立趨勢資料（由舊到新）。
 * @param {string} field 場次欄位
 * @param {Array} [sessions] /api/sessions 的 sessions（由新到舊）；省略時用目前區間的 state.sessions
 */
export function seriesFromSessions(field, sessions) {
  return (sessions || state.sessions).slice().reverse().map(function (s) {
    var v = s[field];
    return { date: s.date, value: (typeof v === 'number' && isFinite(v)) ? v : null };
  });
}

// 每場一點的指標（跑力表現、訓練量與強度兩區的大圖與數字卡用）
export var SESSION_TRENDS = {
  distance: { label: '每場距離', field: 'distance_km', unit: 'km', formatter: function (v) { return C.formatNumber(v, 1); } },
  pace: { label: '每場平均配速', field: 'avg_pace_sec_per_km', unit: '/km', formatter: C.formatPace, invert: true },
  avg_hr: { label: '每場平均心率', field: 'avg_hr_bpm', unit: 'bpm', formatter: function (v) { return C.formatNumber(v, 0); } },
  te: { label: '有氧 TE', field: 'aerobic_te', unit: 'TE', formatter: function (v) { return C.formatNumber(v, 1); } }
};
