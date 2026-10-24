/**
 * pace.js — 配速計算機的等速換算：時間字串解析、配速／時速、英里、田徑場分道長度
 *
 * 全是純算術，不打 API。跑力推估（Daniels 公式）不在這裡，走後端
 * /api/tools/race-predict，與課表、戰力綜覽共用同一套公式。
 */

export var KM_PER_MILE = 1.609344;

// World Athletics 400m 標準跑道：彎道半徑 36.5m、道寬 1.22m。第 1 道的丈量線在
// 內緣外 0.30m，其餘各道在分道線外 0.20m——所以第 2 道是 407.04m，不是常見
// 簡化值 407.67m（那是把每道都當成 0.30m 算的）。
var TRACK_INNER_RADIUS_M = 36.5;
var TRACK_LANE_WIDTH_M = 1.22;
var LANE1_MEASURE_OFFSET_M = 0.30;
var OTHER_LANE_MEASURE_OFFSET_M = 0.20;
var TRACK_LENGTH_M = 400;

/** 第 lane 道跑一圈的實際距離（公尺）。 */
export function laneLengthM(lane) {
  if (lane <= 1) return TRACK_LENGTH_M;
  var lane1Radius = TRACK_INNER_RADIUS_M + LANE1_MEASURE_OFFSET_M;
  var radius = TRACK_INNER_RADIUS_M + TRACK_LANE_WIDTH_M * (lane - 1) + OTHER_LANE_MEASURE_OFFSET_M;
  // 只有兩個彎道（合起來一整圈）會變長，直道每道一樣
  return TRACK_LENGTH_M + 2 * Math.PI * (radius - lane1Radius);
}

/**
 * 'h:m:s'／'m:s'／純數字 → 秒。純數字的單位由 plainUnitSec 決定
 * （配速、完成時間填「5」是 5 分鐘；每圈秒數填「96」是 96 秒）。
 * 秒可帶小數（田徑場常見 23.5 秒）。格式不對或 ≤0 回 null。
 * @param {string} text
 * @param {number} plainUnitSec 純數字乘上的秒數（60 或 1）
 * @returns {?number}
 */
export function parseClock(text, plainUnitSec) {
  var raw = String(text || '').trim().replace(/：/g, ':');
  if (raw === '') return null;
  var parts = raw.split(':');
  if (parts.length > 3 || parts.some(function (p) { return !/^\d+(\.\d+)?$/.test(p); })) return null;
  var nums = parts.map(Number);
  var sec;
  if (nums.length === 1) {
    sec = nums[0] * plainUnitSec;
  } else {
    // 分、秒欄位不可超過 59（打成 5:75 多半是打錯，不默默進位）
    if (nums.slice(1).some(function (n) { return n >= 60; })) return null;
    sec = nums.reduce(function (acc, n) { return acc * 60 + n; }, 0);
  }
  return sec > 0 && isFinite(sec) ? sec : null;
}

/**
 * 秒 → 田徑場分段時間：不到一分鐘顯示「23.8」，以上顯示「1:35.2」。
 * 整數秒的時間格式用 charts.js 的 formatPace／formatDuration。
 */
export function formatSplit(sec) {
  if (sec === null || sec === undefined || !isFinite(sec)) return '—';
  var tenths = Math.round(sec * 10);
  var m = Math.floor(tenths / 600);
  var s = (tenths % 600) / 10;
  var sText = s.toFixed(1);
  return m > 0 ? m + ':' + (s < 10 ? '0' : '') + sText : sText;
}

/** 秒 → 「m:ss」，四捨五入到整秒（59.5 秒進位成 1:00）。配速顯示用。 */
export function formatMinSec(sec) {
  if (sec === null || sec === undefined || !isFinite(sec)) return '—';
  var total = Math.round(sec);
  var s = total % 60;
  return Math.floor(total / 60) + ':' + (s < 10 ? '0' : '') + s;
}

/** 每公里秒數 ↔ 時速（km/h）。兩個方向是同一條式子。 */
export function paceToKmh(secPerKm) {
  return 3600 / secPerKm;
}
export var kmhToPace = paceToKmh;
