/**
 * format.js — 格式化與日期工具：差值、時間、週起點（週日／週一）、星期標籤
 */

import * as C from '../charts.js';
import { WORKOUT_TYPE_LABEL } from './state.js';

/**
 * 某天的暖身：主課 id → 暖身活動（/api/calendar 的 warmup_for_activity_id，N-25）。
 * 學員常把暖身與主課分開按錶，後端已關聯；前端據此把暖身折進主課或標示為暖身。
 */
export function warmupsByMain(activities) {
  var map = {};
  (activities || []).forEach(function (a) {
    if (a.warmup_for_activity_id) map[a.warmup_for_activity_id] = a;
  });
  return map;
}

/** '含暖身 1.3 km' */
export function warmupLabel(warmup) {
  return '含暖身 ' + C.formatNumber(warmup.distance_km || 0, 1) + ' km';
}

export function fmtDelta(v, decimals, unit) {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  var n = Number(v);
  var sign = n > 0 ? '+' : '';
  return sign + n.toFixed(decimals === undefined ? 0 : decimals) + (unit || '');
}

export function deltaClass(v) {
  if (v === null || v === undefined || !isFinite(v)) return '';
  if (v > 0) return ' delta-up';
  if (v < 0) return ' delta-down';
  return '';
}

/**
 * 取該日期所在週的週日（UTC）。行事曆與本週條的一週起點。
 *
 * 【為什麼與 weeklyVolume() 的週一不一致，是刻意的】
 * 「一週從哪天開始」在本專案有兩個不同語意：
 *   - 行事曆呈現：跟隨使用者習慣的日曆排法（週日為首），純粹是看的方式
 *   - 週跑量統計：訓練科學慣例用 ISO 週（週一起算），換算週負荷、
 *     ACWR 都以此為準，改動會讓歷史數字與既有規則引擎對不上
 * 兩者各自保留自己的函式，不要為了「統一」而把其中一邊改掉。
 */
export function sundayOf(dateStr) {
  var d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - d.getUTCDay()); // getUTCDay() 0=週日，直接回推
  return d;
}

/** 取該日期所在 ISO 週的週一（UTC）。僅供週跑量等訓練負荷統計使用。 */
export function mondayOf(dateStr) {
  var d = new Date(dateStr + 'T00:00:00Z');
  var dow = d.getUTCDay();
  var offset = dow === 0 ? 6 : dow - 1;
  d.setUTCDate(d.getUTCDate() - offset);
  return d;
}

export function addDaysISO(dateStr, days) {
  var d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * 瀏覽器本地時區的今天（YYYY-MM-DD）。
 * 不可用 toISOString()：那是 UTC 日期，台灣（UTC+8）08:00 前會得到昨天。
 */
export function todayISO() {
  var d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    + '-' + String(d.getDate()).padStart(2, '0');
}

export function dowLabel(dateStr) {
  var names = ['日', '一', '二', '三', '四', '五', '六'];
  return names[new Date(dateStr + 'T00:00:00Z').getUTCDay()];
}

/** 'YYYY-MM-DDTHH:MM…' → 'M/D HH:MM'。帶 Z 的 UTC 時間會換算成瀏覽器當地時間。 */
export function shortDateTime(iso) {
  if (!iso) return '—';
  if (/Z$/.test(iso)) {
    var d = new Date(iso);
    return (d.getMonth() + 1) + '/' + d.getDate() + ' '
      + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  return C.shortDate(iso.slice(0, 10)) + ' ' + iso.slice(11, 16);
}

/**
 * 教練原文的精簡版：去掉開頭的「D2：」編號，並在第一個全形括號（我們寫入時
 * 附的換算說明）之前截斷。完整原文放在 title，hover／長按看得到。
 */
export function shortPlanNote(notes) {
  if (!notes) return '';
  var text = notes.replace(/^D\d+[：:]\s*/, '');
  var cut = text.indexOf('（');
  return cut > 0 ? text.slice(0, cut) : text;
}

export function planSummary(p) {
  var parts = [WORKOUT_TYPE_LABEL[p.workout_type] || p.workout_type];
  if (p.planned_distance_km) parts.push(C.formatNumber(p.planned_distance_km, 1) + ' km');
  else if (p.planned_duration_sec) parts.push(Math.round(p.planned_duration_sec / 60) + ' 分');
  if (p.planned_pace_sec_per_km) parts.push('@' + C.formatPace(p.planned_pace_sec_per_km));
  return parts.join(' ');
}
