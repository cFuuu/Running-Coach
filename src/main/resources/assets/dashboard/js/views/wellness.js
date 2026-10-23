/**
 * wellness.js — 趨勢頁每日身體指標的資料轉換（畫圖與歸類見 views/trends.js）
 */

import * as C from '../charts.js';

/** 依 def.format 選對應的格式化函式（'duration' 用時分秒，其餘用一般數字）。 */
export function wellnessFormatter(def) {
  if (def.format === 'duration') return function (v) { return C.formatDuration(v); };
  return function (v) { return C.formatNumber(v, def.decimals); };
}

/**
 * 某個每日指標在 /api/wellness-trend 回傳中的有值點（由舊到新）。
 * 'duration' 格式（秒）換成小時，圖表的 Y 軸才不會和分數類共用一個 0–30000 的刻度。
 * @returns {Array} [{date, value}]；指標不存在時為空陣列
 */
export function wellnessPoints(wellness, def) {
  var m = wellness && wellness.metrics && wellness.metrics[def.key];
  if (!m || m.available === false) return [];
  var scale = def.format === 'duration' ? 1 / 3600 : 1;
  return (m.points || []).filter(function (p) {
    return typeof p.value === 'number' && isFinite(p.value);
  }).map(function (p) { return { date: p.date, value: p.value * scale }; });
}

/** 指標被資料起日截斷（clipped）時的說明文字；沒截斷回 null。不把缺的部分畫成 0。 */
export function wellnessClipNote(wellness, def) {
  var m = wellness && wellness.metrics && wellness.metrics[def.key];
  if (!m || !m.clipped) return null;
  var cov = m.coverage && m.coverage.earliest_date;
  return def.label + '：' + (cov ? cov + ' 前無資料' : '此日期前無資料');
}
