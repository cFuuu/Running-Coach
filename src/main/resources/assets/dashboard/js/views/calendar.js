/**
 * calendar.js — 行事曆 view（E.1）：月曆格（週日起排）、每筆活動一顆運動膠囊、
 *               用膠囊外框表示課表狀態、ⓘ 圖例、頂部三數字與底部各運動合計，
 *               點日期後在下方展開當天課表／實際內容（含「這場是課表」確認）與所屬課表週。
 *               另匯出首頁週條共用的活動色點。
 *
 * 只呈現、不判讀：課表配對結果一律取自 /api/calendar 的 plan_match（後端
 * plan_reconciliation），暖身合併取自 warmup_for_activity_id（N-25），前端不另寫判定規則。
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, ACTIVITY_TYPE_LABEL, activityColorVar, DAY_STATE_LABEL } from '../core/state.js';
import { api, apiSend, isOfflineSnapshot } from '../core/api.js';
import { $, clear, h, showLoading, showError } from '../core/dom.js';
import { sundayOf, mondayOf, addDaysISO, dowLabel, planSummary, shortPlanNote } from '../core/format.js';
import { infoTip } from '../core/facts.js';
import { sportOf, sportIconNode, SPORT_KEYS, SPORT_LABEL } from '../core/sport-icons.js';
import { navigate, renderOrDefer } from '../shell/router.js';
import { planWeekRange, loadPlanWeek, planWeekLabel, renderPlanWeek } from './plan-week.js';
import { loadHome } from './home.js';

// 週條與行事曆共用 /api/calendar（start/end 起訖日），刻意不走 range
// 系統——這裡永遠錨定真實今天，與其他 range 圖表錨定「資料庫最新日」
// 的語意不同（見後端 dashboard_queries.get_calendar 說明）。

/** 建立一格週條／行事曆格內的活動色點列（全部運動類型，見 get_calendar）。首頁週條用。 */
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

// ------------------------------------------------------------ 膠囊與當日模型

// 一格最多顯示幾顆活動膠囊，其餘收成「還有 N 筆」；手機（CSS 斷點）只顯示 2 顆＋「+N」
var CELL_MAX_DESKTOP = 3;
var CELL_MAX_PHONE = 2;

// 課表課種 → 呈現用的運動種類（ghost 膠囊的顏色）。比照後端 plan_reconciliation：
// 跑步課種、strength、cross_training 以外（rest 與未知課種）不畫膠囊。
var RUNNING_PLAN_TYPES = { easy: 1, tempo: 1, interval: 1, lsd: 1, race: 1 };

function planSport(plan) {
  var wt = plan.workout_type;
  if (wt === 'race') return 'race';
  if (RUNNING_PLAN_TYPES[wt]) return 'run';
  if (wt === 'strength') return 'strength';
  if (wt === 'cross_training') return 'other';
  return null;
}

/**
 * 課表與活動的運動種類是否相容——只用來判斷「plan_match 非 none 的那筆，配的是不是這份課表」
 * （一天有多份課表時），規則與後端 plan_reconciliation.is_compatible 相同。
 */
function planAccepts(plan, activity) {
  var sport = sportOf(activity.activity_type);
  var wt = plan.workout_type;
  if (RUNNING_PLAN_TYPES[wt]) return sport === 'run';
  if (wt === 'strength') return sport === 'strength';
  if (wt === 'cross_training') return sport !== 'run' && sport !== 'strength';
  return false;
}

/**
 * 膠囊上的數值：有距離顯示公里（一位小數）；沒有距離顯示時間——未滿 1 小時為 分:秒，
 * 1 小時以上為小時（一位小數，例 1.1h）。
 */
export function chipValue(distanceKm, durationSec) {
  if (distanceKm && distanceKm > 0) return C.formatNumber(distanceKm, 1);
  if (durationSec && durationSec > 0) {
    if (durationSec < 3600) return C.formatDuration(durationSec);
    return C.formatNumber(durationSec / 3600, 1) + 'h';
  }
  return '—';
}

/**
 * 一顆運動膠囊。
 * @param {string} sport SPORT_KEYS 之一
 * @param {string|null} text 膠囊文字（null＝只有圖示）
 * @param {string} [extra] 額外 class（is-done／is-planned／is-missed／is-race-planned／cal-pending…）
 */
function sportChip(sport, text, extra) {
  var chip = h('span', 'sport-chip sport-chip--' + sport + (extra ? ' ' + extra : ''));
  chip.appendChild(sportIconNode(sport));
  if (text !== null && text !== undefined) chip.appendChild(h('span', 'cal-chip-text', text));
  return chip;
}

/** 空的課表膠囊（未到／未執行／賽事）。文字包在 .cal-ghost-text，手機上隱藏只留外框。 */
function ghostChip(ghost) {
  var cls = ghost.kind === 'missed' ? 'is-missed' : (ghost.kind === 'race' ? 'is-race-planned' : 'is-planned');
  var chip = h('span', 'sport-chip sport-chip--' + ghost.sport + ' ' + cls + ' cal-ghost');
  if (ghost.kind === 'race') chip.appendChild(sportIconNode('race'));
  chip.appendChild(h('span', 'cal-ghost-text', ghost.label));
  chip.title = ghost.title || ghost.label;
  return chip;
}

/**
 * 某天的呈現模型：暖身折進主課（合計距離與時間）、依「配到課表的排第一、其餘依開始時間」
 * 排序（後端已依 started_at 排好），並算出要畫的空課表膠囊。
 * @returns {{entries: Array, ghosts: Array}}
 */
function dayModel(day, today, raceByDate) {
  var acts = day.activities || [];
  var present = {};
  acts.forEach(function (a) { present[a.id] = true; });
  var warmupOf = {};
  acts.forEach(function (a) {
    if (a.warmup_for_activity_id && present[a.warmup_for_activity_id]) warmupOf[a.warmup_for_activity_id] = a;
  });

  var entries = [];
  acts.forEach(function (a, idx) {
    if (a.warmup_for_activity_id && present[a.warmup_for_activity_id]) return; // 併入主課
    var warmup = warmupOf[a.id] || null;
    var match = a.plan_match || { state: 'none' };
    entries.push({
      act: a,
      warmup: warmup,
      order: idx,
      sport: sportOf(a.activity_type, a.workout_type),
      distance: (a.distance_km || 0) + (warmup ? (warmup.distance_km || 0) : 0),
      duration: (a.duration_sec || 0) + (warmup ? (warmup.duration_sec || 0) : 0),
      matched: match.state === 'exact' || match.state === 'inferred',
      pending: match.state === 'ambiguous',
      linked: match.method === 'linked_activity_id'
    });
  });
  entries.sort(function (x, y) {
    if (x.matched !== y.matched) return x.matched ? -1 : 1;
    return x.order - y.order;
  });

  var ghosts = [];
  var raceShown = false;
  (day.planned || []).forEach(function (plan) {
    var sport = planSport(plan);
    if (!sport) return;
    var executed = entries.some(function (e) {
      return (e.matched || e.pending) && planAccepts(plan, e.act);
    });
    if (executed) return;
    var race = raceByDate[day.date];
    if (plan.workout_type === 'race' && day.date >= today) {
      var name = race ? race.name : (shortPlanNote(plan.notes) || '賽事');
      ghosts.push({ kind: 'race', sport: 'race', label: name, title: '賽事：' + name });
      raceShown = true;
    } else if (day.date >= today) {
      ghosts.push({ kind: 'planned', sport: sport, label: '課表', title: '課表：' + planSummary(plan) });
    } else {
      ghosts.push({ kind: 'missed', sport: sport, label: '未執行', title: '未執行：' + planSummary(plan) });
    }
  });
  // 學員檔賽程有、但課表沒排比賽日的未來賽事，也畫一顆賽事膠囊
  var scheduled = raceByDate[day.date];
  if (scheduled && !raceShown && day.date >= today
      && !entries.some(function (e) { return e.sport === 'race'; })) {
    ghosts.push({ kind: 'race', sport: 'race', label: scheduled.name, title: '賽事：' + scheduled.name });
  }
  return { entries: entries, ghosts: ghosts };
}

function entryChip(entry) {
  var cls = entry.matched ? 'is-done' : (entry.pending ? 'cal-pending' : '');
  var chip = sportChip(entry.sport, chipValue(entry.distance, entry.duration), cls);
  var a = entry.act;
  chip.title = (SPORT_LABEL[entry.sport] || '') + '　' + (a.title || '')
    + (entry.pending ? '（待確認是否為課表）' : '');
  return chip;
}

/** 學員檔賽程（/api/today 的 races.all）依日期索引。 */
function raceIndex() {
  var races = (state.today && state.today.races) || state.calendarRaces;
  var map = {};
  ((races && races.all) || []).forEach(function (r) { if (r && r.date) map[r.date] = r; });
  return map;
}

/**
 * 未來賽事的名稱取自 /api/today 的 races（學員檔賽程，後端 race_names.race_countdown）。
 * 首頁已載入就直接用；直接開行事曆時補抓一次，抓不到就只靠課表的 race 課種與 notes。
 */
function ensureRaces() {
  if ((state.today && state.today.races) || state.calendarRaces) return;
  state.calendarRaces = { all: [] }; // 只抓一次（含失敗）
  api('/api/today').then(function (data) {
    state.calendarRaces = (data && data.races) || { all: [] };
    if (state.calendarData) renderOrDefer('calendar', renderCalendarBody);
  }).catch(function () { /* 沒有賽程不影響月曆其餘內容 */ });
}

// ------------------------------------------------------------ 頂部數字／連續訓練週數

function monthDays(range) {
  return ((state.calendarData && state.calendarData.days) || []).filter(function (d) {
    return d.date >= range.monthStart && d.date <= range.monthEnd;
  });
}

/** 當月合計：跑量（跑步類 activity_type，含暖身）、活動次數（暖身併入主課不另計）、各運動種類合計。 */
function monthTotals(range) {
  var totals = { runKm: 0, count: 0, bySport: {} };
  monthDays(range).forEach(function (day) {
    var acts = day.activities || [];
    var present = {};
    acts.forEach(function (a) { present[a.id] = true; });
    acts.forEach(function (a) {
      // 合計依 activity_type 分類（比賽也是跑步）；顏色上的「賽事」只在膠囊上呈現
      var sport = sportOf(a.activity_type);
      var t = totals.bySport[sport] || (totals.bySport[sport] = { km: 0, sec: 0, count: 0 });
      t.km += a.distance_km || 0;
      t.sec += a.duration_sec || 0;
      if (sport === 'run') totals.runKm += a.distance_km || 0;
      if (a.warmup_for_activity_id && present[a.warmup_for_activity_id]) return;
      t.count += 1;
      totals.count += 1;
    });
  });
  return totals;
}

// 連續訓練週數往回抓的單位（週）。一次 26 週的 /api/calendar；連續週數碰到邊界才再往前抓。
var STREAK_CHUNK_WEEKS = 26;
var STREAK_MAX_CHUNKS = 20; // 最多往回約 10 年，避免資料異常時無限往前抓
var streakLoading = null;

/**
 * 連續訓練 N 週：週一～週日為一週，該週有任何一種活動就延續；本週還沒有活動不算中斷，
 * 從上週往回算。錨定 /api/calendar 回傳的 today（真實今天），與正在看的月份無關，
 * 所以只算一次、存在 state.calendarStreak。
 */
function ensureStreak(today) {
  if (state.calendarStreak && state.calendarStreak.today === today) return;
  if (streakLoading === today) return;
  streakLoading = today;

  var thisMonday = mondayOf(today).toISOString().slice(0, 10);
  var activeWeeks = {};
  var fetchedFrom = addDaysISO(today, 1); // 尚未抓到的最早日（不含）

  function count() {
    var n = activeWeeks[thisMonday] ? 1 : 0;
    var w = addDaysISO(thisMonday, -7);
    while (w >= fetchedFrom && activeWeeks[w]) { n += 1; w = addDaysISO(w, -7); }
    // 停在已抓範圍內的空週＝連續中斷；停在邊界外＝還要往前抓
    return { n: n, needMore: w < fetchedFrom };
  }

  function step(chunk) {
    var end = addDaysISO(fetchedFrom, -1);
    var start = addDaysISO(thisMonday, -7 * STREAK_CHUNK_WEEKS * (chunk + 1));
    return api('/api/calendar?start=' + start + '&end=' + end).then(function (data) {
      (data.days || []).forEach(function (d) {
        if (d.activities && d.activities.length > 0) {
          activeWeeks[mondayOf(d.date).toISOString().slice(0, 10)] = true;
        }
      });
      fetchedFrom = start;
      var c = count();
      if (c.needMore && chunk + 1 < STREAK_MAX_CHUNKS) return step(chunk + 1);
      return c.n;
    });
  }

  step(0).then(function (n) {
    state.calendarStreak = { today: today, weeks: n };
  }).catch(function () {
    state.calendarStreak = { today: today, weeks: null };
  }).then(function () {
    streakLoading = null;
    if (state.calendarData) renderOrDefer('calendar', renderCalendarBody);
  });
}

function statBlock(label, value, unit) {
  var box = h('div', 'cal-stat');
  box.appendChild(h('div', 'cal-stat-label', label));
  var v = h('div', 'cal-stat-value', value);
  if (unit) v.appendChild(h('span', 'cal-stat-unit', unit));
  box.appendChild(v);
  return box;
}

// ------------------------------------------------------------ ⓘ 圖例

function legendRow(chip, text) {
  var row = h('div', 'cal-legend-row');
  var holder = h('span', 'cal-legend-chip');
  holder.appendChild(chip);
  row.appendChild(holder);
  row.appendChild(h('span', 'cal-legend-text', text));
  return row;
}

function legendContent() {
  var nodes = [];
  nodes.push(h('h5', 'cal-legend-head', '運動種類'));
  var sports = h('div', 'cal-legend-sports');
  SPORT_KEYS.forEach(function (key) { sports.appendChild(sportChip(key, SPORT_LABEL[key])); });
  nodes.push(sports);

  nodes.push(h('h5', 'cal-legend-head', '課表狀態（看外框）'));
  var states = h('div', 'cal-legend-states');
  states.appendChild(legendRow(sportChip('run', '8.2', 'is-done'), '有課表，已執行（只框配到課表的那一筆）'));
  states.appendChild(legendRow(sportChip('run', '5.0'), '沒排課的額外活動'));
  states.appendChild(legendRow(sportChip('run', '6.2', 'cal-pending'), '待確認：同一天有多筆都可能是課表，點日期後選「這場是課表」'));
  states.appendChild(legendRow(ghostChip({ kind: 'planned', sport: 'run', label: '課表' }), '有課表，日期還沒到'));
  states.appendChild(legendRow(ghostChip({ kind: 'missed', sport: 'run', label: '未執行' }), '有課表，沒有執行（例：課表是跑步、當天只做了健身）'));
  states.appendChild(legendRow(ghostChip({ kind: 'race', sport: 'race', label: '賽事名稱' }), '賽事，日期還沒到'));
  nodes.push(states);

  nodes.push(h('p', 'cal-legend-note',
    '膠囊數值：有距離顯示公里（一位小數）；健身等沒有距離的顯示時間，未滿 1 小時為 分:秒，'
    + '1 小時以上為小時（例 1.1h）。跑前暖身已併入主課。顏色只代表運動種類，不代表課種。'));
  return nodes;
}

// ------------------------------------------------------------ 月曆本體

function monthTitle(yearMonth) {
  return yearMonth.slice(0, 4) + ' 年 ' + parseInt(yearMonth.slice(5, 7), 10) + ' 月';
}

export function renderCalendarBody() {
  var node = $('calendar-body');
  clear(node);
  if (!state.calendarData) { showLoading(node, '載入月曆'); return; }

  var range = monthGridRange(state.calendarMonth);
  var today = state.calendarData.today;
  ensureRaces();
  ensureStreak(today);
  var races = raceIndex();

  // ---- 標題列：月份＋ⓘ圖例，右側換月
  var head = h('div', 'cal-head');
  var titleWrap = h('div', 'cal-title-wrap');
  titleWrap.appendChild(h('h3', 'cal-title', monthTitle(state.calendarMonth)));
  titleWrap.appendChild(infoTip('行事曆圖例', legendContent()));
  head.appendChild(titleWrap);
  var nav = h('div', 'cal-nav');
  var prevBtn = h('button', 'calendar-nav-btn', '‹');
  prevBtn.type = 'button';
  prevBtn.setAttribute('aria-label', '上個月');
  prevBtn.addEventListener('click', function () { shiftCalendarMonth(-1); });
  var nextBtn = h('button', 'calendar-nav-btn', '›');
  nextBtn.type = 'button';
  nextBtn.setAttribute('aria-label', '下個月');
  nextBtn.addEventListener('click', function () { shiftCalendarMonth(1); });
  nav.appendChild(prevBtn);
  nav.appendChild(nextBtn);
  head.appendChild(nav);
  node.appendChild(head);

  // ---- 頂部三數字
  var totals = monthTotals(range);
  var stats = h('div', 'cal-stats');
  stats.appendChild(statBlock('本月跑量', C.formatNumber(totals.runKm, 1), ' km'));
  stats.appendChild(statBlock('本月活動', String(totals.count), ' 次'));
  var streak = state.calendarStreak && state.calendarStreak.today === today ? state.calendarStreak.weeks : undefined;
  stats.appendChild(statBlock('連續訓練',
    streak === undefined ? '…' : (streak === null ? '—' : String(streak)),
    streak === undefined || streak === null ? null : ' 週'));
  node.appendChild(stats);

  // ---- 星期標頭（週日起）
  var dowHead = h('div', 'calendar-dow-head cal-dow-head');
  ['日', '一', '二', '三', '四', '五', '六'].forEach(function (name) {
    dowHead.appendChild(h('span', 'calendar-dow', name));
  });
  node.appendChild(dowHead);

  var grid = h('div', 'calendar-grid cal-grid');
  var byDate = {};
  (state.calendarData.days || []).forEach(function (d) { byDate[d.date] = d; });

  var cursor = range.start;
  while (cursor <= range.end) {
    var day = byDate[cursor];
    var cell = h('div', 'calendar-cell cal-cell');
    cell.dataset.date = cursor;
    if (cursor < range.monthStart || cursor > range.monthEnd) cell.classList.add('is-outside-month');
    var dateLabel = h('span', 'cal-date' + (cursor === today ? ' is-today' : ''), String(parseInt(cursor.slice(8, 10), 10)));
    cell.appendChild(dateLabel);
    if (day) {
      // 有資料的日子都可點——點擊是「看這天所屬的課表週與當天發生什麼」
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
      cell.appendChild(renderCellChips(day, today, races));
      cell.setAttribute('aria-label', cursor + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state)
        + '　' + (day.activities || []).length + ' 筆活動');
    }
    grid.appendChild(cell);
    cursor = addDaysISO(cursor, 1);
  }
  node.appendChild(grid);

  // ---- 底部：當月各運動種類合計
  node.appendChild(renderMonthFooter(totals));

  markCalendarSelection();
  // 月曆重畫後明細要跟著更新（換月、或首次載入都會走到這裡）
  renderCalendarDay();
}

/** 格內的膠囊：活動（超過上限收合）＋空課表膠囊。 */
function renderCellChips(day, today, races) {
  var wrap = h('div', 'cal-chips');
  var model = dayModel(day, today, races);
  model.entries.forEach(function (entry, idx) {
    var chip = entryChip(entry);
    if (idx >= CELL_MAX_DESKTOP) chip.classList.add('cal-hide-desk');
    if (idx >= CELL_MAX_PHONE) chip.classList.add('cal-hide-phone');
    wrap.appendChild(chip);
  });
  var n = model.entries.length;
  if (n > CELL_MAX_DESKTOP) {
    wrap.appendChild(h('span', 'cal-more cal-more--desk', '還有 ' + (n - CELL_MAX_DESKTOP) + ' 筆'));
  }
  if (n > CELL_MAX_PHONE) {
    wrap.appendChild(h('span', 'cal-more cal-more--phone', '+' + (n - CELL_MAX_PHONE)));
  }
  model.ghosts.forEach(function (g) { wrap.appendChild(ghostChip(g)); });
  return wrap;
}

function renderMonthFooter(totals) {
  var foot = h('div', 'cal-foot');
  ['run', 'walk', 'bike', 'hike', 'swim', 'strength', 'other'].forEach(function (sport) {
    var t = totals.bySport[sport];
    if (!t || t.count === 0) return;
    var item = h('div', 'cal-foot-item');
    item.appendChild(sportChip(sport, null));
    var text;
    if (sport === 'strength' || (sport === 'other' && t.km === 0)) {
      text = t.count + ' 次 · ' + chipValue(0, t.sec);
    } else {
      text = C.formatNumber(t.km, 1) + ' km';
    }
    item.appendChild(h('span', 'cal-foot-val', text));
    item.title = SPORT_LABEL[sport] + '　' + t.count + ' 次';
    foot.appendChild(item);
  });
  if (!foot.firstChild) foot.appendChild(h('span', 'cal-foot-empty', '本月還沒有活動'));
  return foot;
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
  state.calendarLinkMessage = null;
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

// ------------------------------------------------------------ 當日明細

/** 'D/M（四）' */
function dayTitle(date) {
  return C.shortDate(date) + '（' + dowLabel(date) + '）';
}

/** 實際活動一列的摘要文字：種類 距離（主課＋暖身）· 配速 · 心率，或時間。 */
function entrySummary(entry) {
  var a = entry.act;
  var parts = [];
  var label = ACTIVITY_TYPE_LABEL[a.activity_type] || SPORT_LABEL[sportOf(a.activity_type)] || a.activity_type;
  if (entry.distance > 0) {
    var dist = label + ' ' + C.formatNumber(entry.distance, 1) + ' km';
    if (entry.warmup) {
      dist += '（主課 ' + C.formatNumber(a.distance_km || 0, 1) + '＋暖身 '
        + C.formatNumber(entry.warmup.distance_km || 0, 1) + '）';
    }
    parts.push(dist);
    // 配速只對跑步有意義（自行車的 分/公里 讀不出東西）
    if (a.is_running && a.distance_km && a.duration_sec) {
      parts.push(C.formatPace(a.duration_sec / a.distance_km) + '/km');
    }
  } else {
    parts.push(label);
    if (entry.duration > 0) parts.push(C.formatDuration(entry.duration));
  }
  if (a.avg_hr_bpm) parts.push(Math.round(a.avg_hr_bpm) + ' bpm');
  return parts.join(' · ');
}

/** 這筆活動要連到哪一份課表（「這場是課表」用）：當天第一份運動種類相容的課表。 */
function planFor(day, activity) {
  var plans = (day.planned || []).filter(function (p) { return planAccepts(p, activity); });
  return plans.length > 0 ? plans[0] : null;
}

/**
 * 「這場是課表」／取消：PUT /api/plan/{id}/link。成功後重抓月曆（配對與重算後的課種都以
 * 後端為準）；後端的 4xx detail 是可直接顯示的中文說明，原文顯示在面板上。
 */
function sendPlanLink(planId, activityId, button) {
  if (button) button.disabled = true;
  apiSend('PUT', '/api/plan/' + encodeURIComponent(planId) + '/link', { activity_id: activityId })
    .then(function (res) {
      var msg = activityId === null ? '已取消課表連結。' : '已確認這場是課表。';
      var changed = (res && res.reclassified) || [];
      if (changed.length > 0) {
        msg += '　課種已重算：' + changed.map(function (c) {
          return (WORKOUT_TYPE_LABEL[c.old] || c.old || '—') + ' → ' + (WORKOUT_TYPE_LABEL[c.new] || c.new || '—');
        }).join('、');
        // 課種變了：首頁（本週條、最近一場）也要重讀，否則與行事曆不一致
        loadHome();
      }
      state.calendarLinkMessage = { text: msg, kind: 'ok' };
      loadCalendarBody();
    })
    .catch(function (err) {
      state.calendarLinkMessage = { text: err.message, kind: 'error' };
      if (button) button.disabled = false;
      renderCalendarDay();
    });
}

function linkActions(day, entry) {
  var wrap = h('div', 'cal-link-actions');
  var plan = planFor(day, entry.act);
  if (!plan) return null;
  // 確認／取消要寫回伺服器：離線快照只留狀態文字，不放按鈕
  var editable = !isOfflineSnapshot();
  if (entry.linked) {
    wrap.appendChild(h('span', 'cal-link-state', '已確認為課表'));
    if (!editable) return wrap;
    var cancel = h('button', 'cal-link-btn cal-link-btn--cancel', '取消');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { sendPlanLink(plan.id, null, cancel); });
    wrap.appendChild(cancel);
    return wrap;
  }
  if (entry.pending) {
    wrap.appendChild(h('span', 'cal-link-state is-pending', '待確認'));
    if (!editable) return wrap;
    var confirm = h('button', 'cal-link-btn', '這場是課表');
    confirm.type = 'button';
    confirm.addEventListener('click', function () { sendPlanLink(plan.id, entry.act.id, confirm); });
    wrap.appendChild(confirm);
    return wrap;
  }
  return null;
}

/** 月曆下方明細：當天的課表與實際活動，接著是選取日所屬的課表週。 */
export function renderCalendarDay() {
  var panel = $('panel-calendar-day');
  var node = $('calendar-day-body');
  if (!panel || !node) return;
  clear(node);

  var date = state.calendarSelectedDate;
  if (!date) {
    panel.classList.remove('is-open');
    node.appendChild(h('p', 'calendar-day-empty', '點選月曆上的日期，這裡會顯示當天的課表與訓練內容，以及那一週（週一到週日）的執行狀況。'));
    return;
  }
  panel.classList.add('is-open');

  var day = null;
  ((state.calendarData && state.calendarData.days) || []).forEach(function (d) {
    if (d.date === date) day = d;
  });

  var daySec = h('div', 'cal-day');
  if (!day) {
    daySec.appendChild(h('p', 'calendar-day-empty', date + '　沒有資料。'));
  } else {
    var model = dayModel(day, state.calendarData.today, raceIndex());
    var head = h('div', 'calendar-day-head');
    head.appendChild(h('h3', 'calendar-day-title', dayTitle(date) + '　' + model.entries.length + ' 筆活動'));
    head.appendChild(h('span', 'calendar-day-state day-' + day.day_state,
      DAY_STATE_LABEL[day.day_state] || day.day_state));
    daySec.appendChild(head);

    var msg = state.calendarLinkMessage;
    if (msg) {
      var box = h('p', 'cal-link-msg cal-link-msg--' + msg.kind, msg.text);
      box.setAttribute('role', msg.kind === 'error' ? 'alert' : 'status');
      daySec.appendChild(box);
    }

    var cols = h('div', 'cal-day-cols');
    // ---- 課表（教練原文）
    var psec = h('div', 'cal-day-col');
    psec.appendChild(h('h4', 'calendar-day-subtitle', '課表'));
    var plans = day.planned || [];
    if (plans.length === 0) psec.appendChild(h('p', 'cal-day-none', '沒有排課'));
    plans.forEach(function (pl) {
      var row = h('div', 'cal-plan-row');
      row.appendChild(h('div', 'cal-plan-sum', planSummary(pl)));
      if (pl.notes) row.appendChild(h('div', 'cal-plan-notes', pl.notes));
      psec.appendChild(row);
    });
    model.ghosts.forEach(function (g) {
      if (g.kind === 'race') psec.appendChild(h('div', 'cal-plan-row cal-plan-race', '賽事：' + g.label));
    });
    cols.appendChild(psec);

    // ---- 實際
    var asec = h('div', 'cal-day-col');
    asec.appendChild(h('h4', 'calendar-day-subtitle', '實際'));
    if (model.entries.length === 0) asec.appendChild(h('p', 'cal-day-none', '沒有活動紀錄'));
    model.entries.forEach(function (entry) {
      var row = h('div', 'cal-act-row');
      var chipWrap = h('span', 'cal-act-chip');
      chipWrap.appendChild(entryChip(entry));
      row.appendChild(chipWrap);
      var info = h('div', 'cal-act-info');
      info.appendChild(h('div', 'cal-act-sum', entrySummary(entry)));
      if (entry.act.title) info.appendChild(h('div', 'cal-act-title', entry.act.title));
      row.appendChild(info);
      var actions = linkActions(day, entry);
      if (actions) row.appendChild(actions);
      // 開始時間（started_at 為 ISO 當地時間，取 HH:MM）
      var startedAt = entry.act.started_at;
      if (startedAt && startedAt.length >= 16) {
        row.appendChild(h('span', 'cal-act-time', startedAt.slice(11, 16)));
      }
      // 跑步才有單場明細可看：其餘類型（健走、重訓）在訓練紀錄裡沒有對應頁面
      if (entry.act.is_running) {
        var link = h('a', 'cal-act-link', '看單場 ›');
        link.href = '#/sessions/' + entry.act.id;
        row.appendChild(link);
      }
      asec.appendChild(row);
    });
    cols.appendChild(asec);
    daySec.appendChild(cols);
  }
  node.appendChild(daySec);

  // ---- 課表週
  var weekDays = selectedWeekDays(date);
  var weekSec = h('div', 'calendar-week cal-week');
  weekSec.appendChild(h('h3', 'calendar-day-title',
    '課表週　' + (weekDays ? planWeekLabel(weekDays) : planWeekRange(date).start + ' 起')));
  var weekBody = h('div', null);
  weekBody.id = 'calendar-week-body';
  if (weekDays) weekBody.appendChild(renderPlanWeek(weekDays, { selectedDate: date }));
  else showLoading(weekBody, '載入課表週');
  weekSec.appendChild(weekBody);
  node.appendChild(weekSec);
}

export function loadCalendarBody() {
  var range = monthGridRange(state.calendarMonth);
  // 同一個月份重抓（同步完成、確認課表後）＝資料可能變了，連續週數一併重算；
  // 換月時 router 已把 calendarData 清成 null，不會走到這裡的重算
  if (state.calendarData) state.calendarStreak = null;
  api('/api/calendar?start=' + range.start + '&end=' + range.end).then(function (data) {
    state.calendarData = data;
    state.calendarWeek = null;
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
  state.calendarLinkMessage = null;
  navigate('calendar', d.toISOString().slice(0, 7));
}
