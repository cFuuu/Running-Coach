/**
 * format.js — 格式化與日期工具：差值、時間、週起點（週日／週一）、星期標籤
 */

import * as C from '../charts.js';

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

//
// 純呈現層：把 /api/today 的三欄位事實（數值/來源/信賴度）與課表原樣
// 顯示出來，不做任何「今天該不該練」的判讀——判讀留給 MCP 對話中的
// 教練（見 PLAN.md §7.3、mcp/server.py 的 coach 行為約束）。

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

export function todayISO() {
  return new Date().toISOString().slice(0, 10);
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
