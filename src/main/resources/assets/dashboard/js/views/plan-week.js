/**
 * plan-week.js — 課表週（週一到週日）的執行狀況：摘要列＋每日課表／實際／狀態
 *
 * 首頁（本課表週）與行事曆（點選那天所屬的週）共用。
 *
 * 為什麼是週一到週日：課表以一週為一個循環，週末的長距離可能排週六或週日，
 * 比賽通常在週日——用週日起算會把同一個循環切成兩半，「本週課表幾公里」就
 * 對不上。行事曆格子仍照使用者習慣從週日排（那只是看的方式），但所有
 * 「本週」的統計一律用這裡的課表週（2026-10-21 與 Fu 議定）。
 *
 * 狀態只描述有沒有對應的活動，不評斷練得好不好（判讀屬 AI 教練）。
 */

import * as C from '../charts.js';
import { ACTIVITY_TYPE_LABEL, DAY_STATE_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { h } from '../core/dom.js';
import { mondayOf, addDaysISO, dowLabel, planSummary, shortPlanNote } from '../core/format.js';
import { navigate } from '../shell/router.js';

/** 某日期所屬課表週的起訖（週一、週日）。 */
export function planWeekRange(dateStr) {
  var start = mondayOf(dateStr).toISOString().slice(0, 10);
  return { start: start, end: addDaysISO(start, 6) };
}

/** 取某日期所屬課表週的 /api/calendar 資料，回傳 days 陣列。 */
export function loadPlanWeek(dateStr) {
  var r = planWeekRange(dateStr);
  return api('/api/calendar?start=' + r.start + '&end=' + r.end).then(function (data) {
    return data.days || [];
  });
}

/** '10/19（一）–10/25（日）' */
export function planWeekLabel(days) {
  if (!days || days.length === 0) return '';
  var first = days[0].date;
  var last = days[days.length - 1].date;
  return C.shortDate(first) + '（' + dowLabel(first) + '）–' + C.shortDate(last) + '（' + dowLabel(last) + '）';
}

function isRunningPlan(p) {
  return p.workout_type && p.workout_type !== 'rest';
}

/** 課表與實際的跑量、堂數。只算跑步：課表的休息日與實際的非跑步活動不計。 */
export function planWeekSummary(days) {
  var sum = { plannedKm: 0, doneKm: 0, plannedSessions: 0, doneSessions: 0 };
  (days || []).forEach(function (d) {
    var runningPlans = (d.planned || []).filter(isRunningPlan);
    runningPlans.forEach(function (p) {
      if (p.planned_distance_km) sum.plannedKm += p.planned_distance_km;
    });
    if (runningPlans.length > 0) sum.plannedSessions += 1;
    if (d.day_state === 'planned_done') sum.doneSessions += 1;
    (d.activities || []).forEach(function (a) {
      if (a.is_running && a.distance_km) sum.doneKm += a.distance_km;
    });
  });
  return sum;
}

/** 摘要列的一個數字（標籤＋大數字＋小單位）；首頁也用它追加 Zone 2。 */
export function planWeekFig(label, value, unit) {
  var f = h('div', 'pw-fig');
  f.appendChild(h('span', 'pw-fig-label', label));
  var v = h('span', 'pw-fig-value', value);
  if (unit) v.appendChild(h('small', null, unit));
  f.appendChild(v);
  return f;
}

function summaryNode(days) {
  var s = planWeekSummary(days);
  var wrap = h('div', 'pw-summary');
  var figs = h('div', 'pw-figs');
  figs.appendChild(planWeekFig('實際', C.formatNumber(s.doneKm, 1), 'km'));
  figs.appendChild(planWeekFig('課表', s.plannedKm > 0 ? C.formatNumber(s.plannedKm, 1) : '—', s.plannedKm > 0 ? 'km' : ''));
  figs.appendChild(planWeekFig('完成', s.doneSessions + '／' + s.plannedSessions, '堂'));
  wrap.appendChild(figs);

  if (s.plannedKm > 0) {
    var pct = Math.min(100, (s.doneKm / s.plannedKm) * 100);
    var bar = h('div', 'kpi-bar');
    var fill = h('div', 'kpi-bar-fill');
    fill.style.width = pct.toFixed(1) + '%';
    bar.appendChild(fill);
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', '實際 ' + C.formatNumber(s.doneKm, 1) + ' km，課表 '
      + C.formatNumber(s.plannedKm, 1) + ' km，' + Math.round(pct) + '%');
    wrap.appendChild(bar);
  }
  return wrap;
}

/** 當天的實際：單場跑步顯示距離與配速並可點進單場分析，多場只加總距離。 */
function actualNode(day) {
  var runs = (day.activities || []).filter(function (a) { return a.is_running; });
  var others = (day.activities || []).filter(function (a) { return !a.is_running; })
    .map(function (a) { return ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type; });
  var cell = h('span', 'wp-actual');
  if (runs.length === 0 && others.length === 0) {
    cell.textContent = '—';
    return cell;
  }
  if (runs.length > 0) {
    var km = runs.reduce(function (t, a) { return t + (a.distance_km || 0); }, 0);
    var text = C.formatNumber(km, 1) + ' km';
    var one = runs.length === 1 ? runs[0] : null;
    if (one && one.distance_km && one.duration_sec) {
      text += '　' + C.formatPace(one.duration_sec / one.distance_km) + '/km';
    }
    var btn = h('button', 'wp-actual-link', text);
    btn.type = 'button';
    btn.title = '看單場分析';
    btn.addEventListener('click', function () { navigate('sessions', runs[0].id); });
    cell.appendChild(btn);
  }
  if (others.length > 0) cell.appendChild(h('span', 'wp-other', others.join('、')));
  return cell;
}

/**
 * 課表週的完整呈現：摘要列＋每天一列。
 * @param {Array} days /api/calendar 的 days（週一到週日）
 * @param {Object} [opts] { selectedDate: 'YYYY-MM-DD' } 行事曆用來標出點選的那天
 * @returns {HTMLElement}
 */
export function renderPlanWeek(days, opts) {
  var options = opts || {};
  var wrap = h('div', 'plan-week-block');
  wrap.appendChild(summaryNode(days));

  var list = h('div', 'week-plan-list');
  var head = h('div', 'wp-row wp-head');
  ['', '日期', '課表', '實際', '狀態'].forEach(function (t) { head.appendChild(h('span', null, t)); });
  list.appendChild(head);

  days.forEach(function (day) {
    var row = h('div', 'wp-row day-' + day.day_state
      + (day.date === options.selectedDate ? ' is-selected' : ''));
    row.appendChild(h('span', 'wp-dow', dowLabel(day.date)));
    row.appendChild(h('span', 'wp-date', C.shortDate(day.date)));

    var plan = h('span', 'wp-plan');
    var p = (day.planned || [])[0];
    if (!p) {
      plan.textContent = '—';
    } else {
      plan.appendChild(h('span', 'wp-plan-main' + (p.workout_type === 'rest' ? ' plan-rest' : ''), planSummary(p)));
      // 休息日的備註只是「Rest」之類的重述，不顯示
      var note = p.workout_type === 'rest' ? '' : shortPlanNote(p.notes);
      if (note) {
        var n = h('span', 'wp-plan-note', note);
        n.title = p.notes;
        plan.appendChild(n);
      }
    }
    row.appendChild(plan);
    row.appendChild(actualNode(day));
    row.appendChild(h('span', 'wp-state', DAY_STATE_LABEL[day.day_state] || day.day_state));
    list.appendChild(row);
  });
  wrap.appendChild(list);
  return wrap;
}
