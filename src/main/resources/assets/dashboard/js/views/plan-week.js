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
import { DAY_STATE_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { h } from '../core/dom.js';
import { mondayOf, addDaysISO, dowLabel, planSummary, shortPlanNote, warmupsByMain, warmupLabel } from '../core/format.js';
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

/** '10/19 (一) ~ 10/25 (日)' */
export function planWeekLabel(days) {
  if (!days || days.length === 0) return '';
  var first = days[0].date;
  var last = days[days.length - 1].date;
  return C.shortDate(first) + ' (' + dowLabel(first) + ') ~ ' + C.shortDate(last) + ' (' + dowLabel(last) + ')';
}

/**
 * 一堂課表的預計里程（km）與是否為推算：有填距離就用距離；只寫時間的課用後端依學員檔
 * 輕鬆配速推算的 estimated_distance_km（公式與配速都不在前端）；兩者都沒有回傳 null。
 */
function plannedKmOf(p) {
  if (p.planned_distance_km) return { km: p.planned_distance_km, estimated: false };
  if (p.estimated_distance_km) return { km: p.estimated_distance_km, estimated: true };
  return null;
}

function isRunningPlan(p) {
  return p.workout_type && p.workout_type !== 'rest';
}

/**
 * 課表與實際的跑量、時間、堂數。只算跑步：課表的休息日與實際的非跑步活動不計。
 * 時間只累計實際（doneSec），不算預計時間：課表的配速多半只是主課配速，
 * 乘上整段距離會漏掉暖身收操與休息，比賽日又沒有配速，分母不可靠，完成率以里程為準。
 */
export function planWeekSummary(days) {
  var sum = {
    plannedKm: 0, doneKm: 0, plannedSessions: 0, doneSessions: 0,
    doneSec: 0,
    doneKmUnplanned: 0, plannedKmEstimated: 0
  };
  (days || []).forEach(function (d) {
    var runningPlans = (d.planned || []).filter(isRunningPlan);
    runningPlans.forEach(function (p) {
      var pk = plannedKmOf(p);
      if (pk) {
        sum.plannedKm += pk.km;
        if (pk.estimated) sum.plannedKmEstimated += pk.km;
      }
    });
    if (runningPlans.length > 0) sum.plannedSessions += 1;
    if (d.day_state === 'planned_done') sum.doneSessions += 1;
    // 課表連時間都沒寫、推算不出里程的日子：分母沒有這天的公里數，
    // 實際公里若算進分子，里程完成率會被灌高，所以另記在 doneKmUnplanned，不進 doneKm。
    var kmUnplanned = runningPlans.length > 0
      && !runningPlans.some(function (p) { return plannedKmOf(p); });
    (d.activities || []).forEach(function (a) {
      if (!a.is_running) return;
      if (a.distance_km) {
        if (kmUnplanned) sum.doneKmUnplanned += a.distance_km;
        else sum.doneKm += a.distance_km;
      }
      if (a.duration_sec) sum.doneSec += a.duration_sec;
    });
  });
  return sum;
}

/** 摘要的一張圖卡：標題＋內容（首頁也用它追加 Zone 2）。 */
export function planWeekTile(label) {
  var tile = h('div', 'pw-tile');
  tile.appendChild(h('div', 'pw-tile-label', label));
  return tile;
}

/** 大數字＋淡化的分母（例如「16.2 / 31.6 km」），trailing 靠最右邊（例如「完成 51%」）。 */
export function planWeekTileValue(big, rest, trailing) {
  var val = h('div', 'pw-tile-value');
  val.appendChild(h('span', 'pw-big', big));
  if (rest) val.appendChild(h('span', 'pw-of', rest));
  if (trailing) val.appendChild(h('span', 'pw-pct', trailing));
  return val;
}

/** 本週跑量區段：實際／課表公里數，下方進度條內直接寫百分比。 */
function kmSection(s) {
  var sec = h('div', 'pw-sec');
  sec.appendChild(h('div', 'pw-tile-label', '本週跑量'));
  var pct = s.plannedKm > 0 ? Math.min(100, (s.doneKm / s.plannedKm) * 100) : null;
  sec.appendChild(planWeekTileValue(C.formatNumber(s.doneKm, 1),
    '/ ' + (s.plannedKm > 0 ? C.formatNumber(s.plannedKm, 1) : '—') + ' km'));
  if (s.plannedKmEstimated > 0) {
    sec.title = '預計距離含 ' + C.formatNumber(s.plannedKmEstimated, 1)
      + ' km 推算值：課表只寫時間的課，以學員檔的輕鬆配速中點換算';
  }
  if (pct !== null) {
    var bar = h('div', 'pw-bar');
    var fill = h('div', 'pw-bar-fill');
    fill.style.width = pct.toFixed(1) + '%';
    bar.appendChild(fill);
    // 百分比寫在 bar 裡：填色段夠長就放在段內右端（白字），太短放不下就接在段後（灰字）
    var label = h('span', 'pw-bar-pct' + (pct >= 18 ? ' is-inside' : ''),
      Math.round(s.doneKm / s.plannedKm * 100) + '%');
    if (pct >= 18) fill.appendChild(label);
    else { label.style.left = 'calc(' + pct.toFixed(1) + '% + 6px)'; bar.appendChild(label); }
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', '實際 ' + C.formatNumber(s.doneKm, 1) + ' km，課表 '
      + C.formatNumber(s.plannedKm, 1) + ' km，完成 ' + Math.round(pct) + '%');
    sec.appendChild(bar);
  }
  return sec;
}

/** 完成堂數區段：數字在左、每堂課表一個圓點在右，已完成的塗滿。 */
function sessionsSection(s) {
  var sec = h('div', 'pw-sec pw-sec-inline');
  var left = h('div');
  left.appendChild(h('div', 'pw-tile-label', '課表完成度'));
  left.appendChild(planWeekTileValue(String(s.doneSessions), '/ ' + s.plannedSessions + ' 堂'));
  sec.appendChild(left);
  var dots = h('div', 'pw-dots');
  for (var i = 0; i < s.plannedSessions; i += 1) {
    dots.appendChild(h('span', 'pw-dot' + (i < s.doneSessions ? ' is-done' : '')));
  }
  dots.setAttribute('role', 'img');
  dots.setAttribute('aria-label', '已完成 ' + s.doneSessions + ' 堂，共 ' + s.plannedSessions + ' 堂');
  sec.appendChild(dots);
  return sec;
}

/** 摘要：里程與完成堂數上下合成一張圖卡（首頁再在右邊加 Zone 2 圖卡）。 */
function summaryNode(days) {
  var s = planWeekSummary(days);
  var wrap = h('div', 'pw-summary');
  var tiles = h('div', 'pw-tiles');
  var tile = h('div', 'pw-tile');
  tile.appendChild(kmSection(s));
  tile.appendChild(sessionsSection(s));
  tiles.appendChild(tile);
  wrap.appendChild(tiles);
  return wrap;
}

/**
 * 當天的實際：只列跑步。單場顯示距離與配速並可點進單場分析，多場只加總距離；健走等其他活動不列。
 * 另存的暖身（N-25）不算一場：距離仍加進總里程，但配速／心率與點擊目標都以主課為準，
 * 並標「含暖身 x km」。
 */
function actualNode(day) {
  var allRuns = (day.activities || []).filter(function (a) { return a.is_running; });
  var runs = allRuns.filter(function (a) { return !a.warmup_for_activity_id; });
  var cell = h('span', 'wp-actual');
  if (runs.length === 0) {
    // is-empty：手機兩行排列時這格獨佔一行，只放一個破折號太浪費，由 CSS 隱藏
    cell.className += ' is-empty';
    cell.textContent = '—';
    return cell;
  }
  var km = allRuns.reduce(function (t, a) { return t + (a.distance_km || 0); }, 0);
  var btn = h('button', 'wp-actual-link', C.formatNumber(km, 1) + ' km');
  var one = runs.length === 1 ? runs[0] : null;
  // 配速與平均心率同一行，灰色小字
  var sub = [];
  var warmup = one ? warmupsByMain(day.activities)[one.id] : null;
  // 「含暖身」獨立一行：這一欄只有 7.6rem，和配速、心率擠在同一行會把心率切掉
  if (warmup) btn.appendChild(h('span', 'wp-sub', warmupLabel(warmup)));
  if (one && one.distance_km && one.duration_sec) {
    sub.push(C.formatPace(one.duration_sec / one.distance_km) + '/km');
  }
  if (one && one.avg_hr_bpm) sub.push(Math.round(one.avg_hr_bpm) + ' bpm');
  if (sub.length) btn.appendChild(h('span', 'wp-sub', sub.join(' · ')));
  btn.type = 'button';
  btn.title = '看單場分析';
  btn.addEventListener('click', function () { navigate('sessions', runs[0].id); });
  cell.appendChild(btn);
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
  ['日期', '課表', '實際', '狀態'].forEach(function (t) { head.appendChild(h('span', null, t)); });
  list.appendChild(head);

  days.forEach(function (day) {
    var row = h('div', 'wp-row day-' + day.day_state
      + (day.date === options.selectedDate ? ' is-selected' : ''));
    row.appendChild(h('span', 'wp-date', C.shortDate(day.date) + ' (' + dowLabel(day.date) + ')'));

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
