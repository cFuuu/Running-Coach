/**
 * calendar.js — 行事曆 view：月曆格（週日起排）、點選日所屬課表週的執行狀況與當日明細、圖例，
 *               以及週條與月曆共用的活動色點
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, ACTIVITY_TYPE_LABEL, activityColorVar, DAY_STATE_LABEL } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError } from '../core/dom.js';
import { sundayOf, addDaysISO, dowLabel } from '../core/format.js';
import { navigate, renderOrDefer } from '../shell/router.js';
import { planWeekRange, loadPlanWeek, planWeekLabel, renderPlanWeek } from './plan-week.js';

// 週條與行事曆共用 /api/calendar（start/end 起訖日），刻意不走 range
// 系統——這裡永遠錨定真實今天，與其他 range 圖表錨定「資料庫最新日」
// 的語意不同（見後端 dashboard_queries.get_calendar 說明）。

/** 建立一格週條／行事曆格內的活動色點列（全部運動類型，見 get_calendar）。 */
export function activityDots(activities) {
  var wrap = h('div', 'week-cell-dots');
  activities.forEach(function (a) {
    var dot = h('span', 'activity-dot');
    dot.style.background = 'var(' + activityColorVar(a.activity_type) + ')';
    dot.title = (ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type)
      + (a.distance_km ? '　' + C.formatNumber(a.distance_km, 1) + ' km' : '');
    wrap.appendChild(dot);
  });
  return wrap;
}

export function firstRunningActivity(activities) {
  for (var i = 0; i < activities.length; i += 1) {
    if (activities[i].is_running) return activities[i];
  }
  return null;
}

/** 行事曆展開時用的月份邊界（YYYY-MM 的第一天所在週日 ~ 最後一天所在週六）。 */
export function monthGridRange(yearMonth) {
  var firstOfMonth = yearMonth + '-01';
  var d = new Date(firstOfMonth + 'T00:00:00Z');
  var nextMonth = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
  var lastOfMonth = new Date(nextMonth.getTime() - 86400000).toISOString().slice(0, 10);
  return {
    start: sundayOf(firstOfMonth).toISOString().slice(0, 10),
    end: (function () {
      var lastRowStart = sundayOf(lastOfMonth);
      lastRowStart.setUTCDate(lastRowStart.getUTCDate() + 6); // 該週的週六
      return lastRowStart.toISOString().slice(0, 10);
    })(),
    monthStart: firstOfMonth,
    monthEnd: lastOfMonth
  };
}

export function renderCalendarBody() {
  var node = $('calendar-body');
  clear(node);
  if (!state.calendarData) { showLoading(node, '載入月曆'); return; }

  var range = monthGridRange(state.calendarMonth);
  var nav = h('div', 'calendar-nav');
  var prevBtn = h('button', 'calendar-nav-btn', '‹');
  prevBtn.type = 'button';
  prevBtn.addEventListener('click', function () { shiftCalendarMonth(-1); });
  var label = h('span', 'calendar-nav-label', state.calendarMonth);
  var nextBtn = h('button', 'calendar-nav-btn', '›');
  nextBtn.type = 'button';
  nextBtn.addEventListener('click', function () { shiftCalendarMonth(1); });
  nav.appendChild(prevBtn);
  nav.appendChild(label);
  nav.appendChild(nextBtn);
  node.appendChild(nav);

  // 星期標頭。原本月曆只有日期數字、沒有星期列，改成週日為首後更需要
  // 明示欄位對應的星期，否則使用者無法確認第一欄到底是週日還是週一。
  var dowHead = h('div', 'calendar-dow-head');
  ['日', '一', '二', '三', '四', '五', '六'].forEach(function (name) {
    dowHead.appendChild(h('span', 'calendar-dow', name));
  });
  node.appendChild(dowHead);

  var grid = h('div', 'calendar-grid');
  var byDate = {};
  (state.calendarData.days || []).forEach(function (d) { byDate[d.date] = d; });

  var cursor = range.start;
  while (cursor <= range.end) {
    var day = byDate[cursor];
    var cell = h('div', 'calendar-cell' + (day ? ' day-' + day.day_state : ''));
    cell.dataset.date = cursor;
    if (cursor < range.monthStart || cursor > range.monthEnd) cell.classList.add('is-outside-month');
    cell.appendChild(h('span', 'calendar-cell-date', cursor.slice(8, 10)));
    if (day) {
      // 有資料的日子都可點——點擊是「看這天所屬的課表週與當天發生什麼」，
      // 空白日也該能點開看整週的執行狀況。
      cell.classList.add('is-clickable');
      cell.setAttribute('role', 'button');
      cell.setAttribute('tabindex', '0');
      (function (date) {
        cell.addEventListener('click', function () { selectCalendarDate(date); });
        // 鍵盤操作：格子是 div，不像 button 會自動把 Enter／Space 轉成 click
        cell.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            selectCalendarDate(date);
          }
        });
      }(cursor));
      if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
      cell.title = cursor + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
    }
    grid.appendChild(cell);
    cursor = addDaysISO(cursor, 1);
  }
  node.appendChild(grid);
  markCalendarSelection();

  // 月曆重畫後明細要跟著更新（換月、或首次載入都會走到這裡）
  renderCalendarDay();
}

/**
 * 選取月曆上的某一天：更新選取態並就地渲染下方的課表週與當日明細。
 * 刻意不改 hash——明細是行事曆的一部分，不是另一個位置；
 * 換月份時 renderCalendarBody 會重畫格子，選取態靠 state 保留。
 * @param {string} date 'YYYY-MM-DD'
 */
export function selectCalendarDate(date) {
  // 再點一次同一天＝收起，符合「點開／點關」的直覺
  state.calendarSelectedDate = (state.calendarSelectedDate === date) ? null : date;
  markCalendarSelection();
  renderCalendarDay();
}

/**
 * 標出選取的那天，以及它所屬課表週（週一到週日）的 7 格。
 * 月曆從週日排，所以一個課表週會跨兩列（週一～週六在上一列、週日在下一列）。
 */
function markCalendarSelection() {
  var grid = $('calendar-body') && $('calendar-body').querySelector('.calendar-grid');
  if (!grid) return;
  var selected = state.calendarSelectedDate;
  var week = selected ? planWeekRange(selected) : null;
  Array.prototype.forEach.call(grid.querySelectorAll('.calendar-cell'), function (c) {
    var d = c.dataset.date;
    c.classList.toggle('is-selected', d === selected);
    c.classList.toggle('in-plan-week', !!week && d >= week.start && d <= week.end);
  });
}

/**
 * 選取日所屬課表週的 7 天。月曆資料通常已涵蓋；課表週超出月曆範圍時
 * （例如月底那週的週日落在下個月）才另外抓，抓回來後重畫明細。
 * @returns {Array|null} null 表示正在載入
 */
function selectedWeekDays(date) {
  var r = planWeekRange(date);
  var byDate = {};
  ((state.calendarData && state.calendarData.days) || []).forEach(function (d) { byDate[d.date] = d; });
  var days = [];
  for (var cursor = r.start; cursor <= r.end; cursor = addDaysISO(cursor, 1)) {
    if (!byDate[cursor]) { days = null; break; }
    days.push(byDate[cursor]);
  }
  if (days) return days;
  if (state.calendarWeek && state.calendarWeek.start === r.start) return state.calendarWeek.days;

  loadPlanWeek(date).then(function (loaded) {
    state.calendarWeek = { start: r.start, days: loaded };
    var sel = state.calendarSelectedDate;
    if (sel && planWeekRange(sel).start === r.start) renderCalendarDay();
  }).catch(function (err) {
    var node = $('calendar-week-body');
    if (node) showError(node, err.message);
  });
  return null;
}

/** 月曆下方明細：選取日所屬的課表週，接著是當天的課表與活動。 */
export function renderCalendarDay() {
  var panel = $('panel-calendar-day');
  var node = $('calendar-day-body');
  if (!panel || !node) return;
  clear(node);

  var date = state.calendarSelectedDate;
  if (!date) {
    panel.classList.remove('is-open');
    node.appendChild(h('p', 'calendar-day-empty', '點選月曆上的日期，這裡會顯示那一週（週一到週日）的課表與執行狀況，以及當天的訓練內容。'));
    return;
  }
  panel.classList.add('is-open');

  // ---- 課表週
  var weekDays = selectedWeekDays(date);
  var weekSec = h('div', 'calendar-week');
  weekSec.appendChild(h('h3', 'calendar-day-title',
    '課表週　' + (weekDays ? planWeekLabel(weekDays) : planWeekRange(date).start + ' 起')));
  var weekBody = h('div', null);
  weekBody.id = 'calendar-week-body';
  if (weekDays) weekBody.appendChild(renderPlanWeek(weekDays, { selectedDate: date }));
  else showLoading(weekBody, '載入課表週');
  weekSec.appendChild(weekBody);
  node.appendChild(weekSec);

  var day = null;
  (state.calendarData && state.calendarData.days ? state.calendarData.days : []).forEach(function (d) {
    if (d.date === date) day = d;
  });
  if (!day) {
    node.appendChild(h('p', 'calendar-day-empty', date + '　沒有資料。'));
    return;
  }

  var head = h('div', 'calendar-day-head');
  head.appendChild(h('h3', 'calendar-day-title', date + '（' + dowLabel(date) + '）'));
  head.appendChild(h('span', 'calendar-day-state day-' + day.day_state,
    DAY_STATE_LABEL[day.day_state] || day.day_state));
  node.appendChild(head);

  // ---- 課表
  if (day.planned && day.planned.length > 0) {
    var psec = h('div', 'calendar-day-section');
    psec.appendChild(h('h4', 'calendar-day-subtitle', '課表'));
    day.planned.forEach(function (pl) {
      var row = h('div', 'calendar-day-row');
      row.appendChild(h('span', 'cd-type', WORKOUT_TYPE_LABEL[pl.workout_type] || pl.workout_type));
      if (pl.planned_distance_km) {
        row.appendChild(h('span', 'cd-val', C.formatNumber(pl.planned_distance_km, 1) + ' km'));
      }
      if (pl.planned_pace_sec_per_km) {
        row.appendChild(h('span', 'cd-val', C.formatPace(pl.planned_pace_sec_per_km) + '/km'));
      }
      psec.appendChild(row);
    });
    node.appendChild(psec);
  }

  // ---- 實際活動
  if (day.activities && day.activities.length > 0) {
    var asec = h('div', 'calendar-day-section');
    asec.appendChild(h('h4', 'calendar-day-subtitle', '實際訓練'));
    day.activities.forEach(function (a) {
      var row = h('div', 'calendar-day-row');
      var dot = h('span', 'cd-dot');
      dot.style.background = 'var(' + activityColorVar(a.activity_type) + ')';
      row.appendChild(dot);
      row.appendChild(h('span', 'cd-type', ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type));
      if (a.title) row.appendChild(h('span', 'cd-title', a.title));
      if (a.distance_km) row.appendChild(h('span', 'cd-val', C.formatNumber(a.distance_km, 2) + ' km'));
      if (a.duration_sec) row.appendChild(h('span', 'cd-val', C.formatDuration(a.duration_sec)));
      if (a.distance_km && a.duration_sec) {
        row.appendChild(h('span', 'cd-val', C.formatPace(a.duration_sec / a.distance_km) + '/km'));
      }
      // 跑步才有單場明細可看：其餘類型（健走、重訓）在訓練紀錄裡沒有對應頁面
      if (a.is_running) {
        var link = h('button', 'cd-link', '看單場分析 →');
        link.type = 'button';
        link.addEventListener('click', function () { navigate('sessions', a.id); });
        row.appendChild(link);
      }
      asec.appendChild(row);
    });
    node.appendChild(asec);
  }

  if ((!day.planned || day.planned.length === 0) && (!day.activities || day.activities.length === 0)) {
    node.appendChild(h('p', 'calendar-day-empty', '這天沒有課表也沒有訓練紀錄。'));
  }
}

export function loadCalendarBody() {
  var range = monthGridRange(state.calendarMonth);
  api('/api/calendar?start=' + range.start + '&end=' + range.end).then(function (data) {
    state.calendarData = data;
    renderOrDefer('calendar', renderCalendarBody);
  }).catch(function (err) {
    showError($('calendar-body'), err.message);
  });
}

export function shiftCalendarMonth(delta) {
  var d = new Date(state.calendarMonth + '-01T00:00:00Z');
  d.setUTCMonth(d.getUTCMonth() + delta);
  // 換月後原本選的那天已不在畫面上，明細跟著收起，避免月曆顯示 10 月
  // 下方卻還停在 9 月某天的內容
  state.calendarSelectedDate = null;
  navigate('calendar', d.toISOString().slice(0, 7));
}

export function renderCalendarLegend() {
  var node = $('calendar-legend');
  clear(node);
  Object.keys(DAY_STATE_LABEL).forEach(function (key) {
    if (key === 'future') return;
    var item = h('span', 'legend-item');
    item.appendChild(h('span', 'legend-swatch day-' + key));
    item.appendChild(document.createTextNode(DAY_STATE_LABEL[key]));
    node.appendChild(item);
  });
}
