/**
 * today.js — 今日 view：今日卡、KPI 條、本週週條與課表一覽、最近訓練
 *
 * 純呈現層：把 /api/today 的三欄位事實（數值/來源/信賴度）與課表原樣
 * 顯示出來，不做任何「今天該不該練」的判讀——判讀留給 MCP 對話中的
 * 教練（見 PLAN.md §7.3、mcp/server.py 的 coach 行為約束）。
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, ACTIVITY_TYPE_LABEL, DAY_STATE_LABEL, CONFIDENCE_LABEL, HRV_STATUS_LABEL, HRV_STATUS_CLASS, RUNNING_ACTIVITY_TYPES_CLIENT } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError } from '../core/dom.js';
import { factValueNode, kpiCard } from '../core/facts.js';
import { fmtDelta, sundayOf, addDaysISO, todayISO, dowLabel } from '../core/format.js';
import { activityDots, firstRunningActivity } from './calendar.js';
import { renderSettingsSync } from './settings.js';
import { navigate } from '../shell/router.js';
import { renderSidebarSync } from '../shell/sync.js';

export function renderTodayPlanRow(label, plan) {
  var row = h('div', 'today-plan-row');
  row.appendChild(h('span', 'today-plan-label', label));
  if (!plan) {
    row.appendChild(h('span', 'today-plan-value', '無課表'));
    return row;
  }
  var text = (WORKOUT_TYPE_LABEL[plan.workout_type] || plan.workout_type || '—');
  if (plan.planned_distance_km !== null && plan.planned_distance_km !== undefined && plan.planned_distance_km > 0) {
    text += '　' + C.formatNumber(plan.planned_distance_km, 1) + ' km';
  }
  if (plan.planned_pace_sec_per_km) {
    text += '　' + C.formatPace(plan.planned_pace_sec_per_km) + '/km';
  }
  row.appendChild(h('span', 'today-plan-value', text));
  if (plan.created_at) {
    row.appendChild(h('span', 'today-note', '課表版本 ' + plan.created_at.slice(0, 10)));
  }
  return row;
}

export function renderTodayReadinessBlock(readiness) {
  var block = h('div', 'today-block');
  block.appendChild(h('div', 'today-block-title', '準備度（規則引擎）'));

  var today = readiness && readiness.today;
  if (!today || today.readiness === null || today.readiness === undefined) {
    var reasons = (today && today.not_assessed || [])
      .map(function (na) { return na.reason; });
    block.appendChild(h('div', 'state-msg state-unavailable',
      reasons.length ? reasons[0] : '今日尚無足夠資料可評估'));
  } else {
    var row = h('div', 'today-fact-row');
    row.appendChild(h('span', 'today-fact-label', '今日判定'));
    row.appendChild(h('span', 'today-fact-value', today.readiness === 'low' ? '低' : '正常'));
    block.appendChild(row);
    (today.triggers || []).forEach(function (t) {
      var trow = h('div', 'today-plan-row');
      trow.appendChild(h('span', 'today-plan-label', ''));
      var detailWrap = h('span', 'today-plan-value', t.detail);
      trow.appendChild(detailWrap);
      var level = (t.confidence && t.confidence.level) || 'high';
      trow.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
      block.appendChild(trow);
    });
  }

  var latest = readiness && readiness.latest_evaluable;
  if (latest && (!today || today.readiness === null || today.readiness === undefined)) {
    block.appendChild(h('div', 'today-note',
      '最近一次可評估：' + latest.date + '　' + (latest.readiness === 'low' ? '低' : '正常')));
  }
  return block;
}

export function renderToday() {
  var node = $('today-body');
  clear(node);
  var data = state.today;
  if (!data) { showLoading(node, '載入今日狀態'); return; }

  if (data.freshness) {
    var f = data.freshness;
    var days = f.days_behind;
    // days_behind 是拿 wellness_latest 與 activity_latest 兩者中較新的
    // 日期去算的（見後端 get_today() 的 most_recent），故顯示文字必須
    // 分開列出兩者，不能只挑其中一個日期配這個天數——挑錯就會出現
    // 「顯示活動日 09-04 卻標『今天』」這種自相矛盾的畫面（曾經發生過）。
    var parts = [];
    if (f.activity_latest) parts.push('活動 ' + f.activity_latest);
    if (f.wellness_latest) parts.push('身體數據 ' + f.wellness_latest);
    var freshText = parts.length > 0
      ? '資料最後同步：' + parts.join('　·　')
        + (days === null || days === undefined ? '' : (days > 0 ? '（' + days + ' 天前）' : '（今天）'))
      : '尚無同步資料';
    node.appendChild(h('div', 'today-freshness', freshText));
  }

  renderSidebarSync();

  var grid = h('div', 'today-grid');

  // 課表卡
  var planBlock = h('div', 'today-block');
  planBlock.appendChild(h('div', 'today-block-title', '課表'));
  var planned = data.planned || {};
  planBlock.appendChild(renderTodayPlanRow('今日', planned.today));
  planBlock.appendChild(renderTodayPlanRow('明日', planned.tomorrow));
  grid.appendChild(planBlock);

  // 準備度卡
  grid.appendChild(renderTodayReadinessBlock(data.readiness));

  // 訓練負荷 + HRV 卡
  var loadBlock = h('div', 'today-block');
  loadBlock.appendChild(h('div', 'today-block-title', '訓練負荷與 HRV'));
  if (!data.load && !data.hrv) {
    loadBlock.appendChild(h('div', 'state-msg state-unavailable', '今日尚無資料可評估'));
  } else {
    if (data.load) {
      ['atl', 'ctl', 'tsb'].forEach(function (key) {
        var row = h('div', 'today-fact-row');
        row.appendChild(h('span', 'today-fact-label', key.toUpperCase()));
        row.appendChild(factValueNode(data.load[key], function (v) { return C.formatNumber(v, 1); }));
        loadBlock.appendChild(row);
      });
    }
    if (data.hrv) {
      // as_of 有值＝顯示的不是今天而是這個日期的資料（今天尚未同步）。
      // 必須明示，否則使用者會把昨天的 HRV 當成今天的。
      if (data.hrv.as_of) {
        loadBlock.appendChild(h('div', 'today-note',
          'HRV 為 ' + data.hrv.as_of + ' 的資料（今日尚未同步）'));
      }
      // 昨夜均值與 7 日均值分開列：Garmin 的狀態與平衡區間是拿 7 日均值
      // 判的（實測 39/39 天吻合，昨夜均值只有 29/39），只列昨夜值會讓人
      // 誤拿它去對平衡區間。
      var hrvRow = h('div', 'today-fact-row');
      hrvRow.appendChild(h('span', 'today-fact-label', 'HRV 昨夜均值'));
      hrvRow.appendChild(factValueNode(data.hrv.today_ms, function (v) { return C.formatNumber(v, 1); }, ' ms'));
      loadBlock.appendChild(hrvRow);
      if (data.hrv.weekly_avg_ms) {
        var weeklyRow = h('div', 'today-fact-row');
        weeklyRow.appendChild(h('span', 'today-fact-label', 'HRV 7 日均值'));
        weeklyRow.appendChild(factValueNode(data.hrv.weekly_avg_ms, function (v) { return C.formatNumber(v, 1); }, ' ms'));
        loadBlock.appendChild(weeklyRow);
      }

      // Garmin 自己的 HRV 狀態（平衡／不平衡）。
      //
      // 這一塊走的是 garmin_reference（for_display_only），不是 fact()
      // 的三欄位契約——它是手錶端黑盒演算法的輸出，規則引擎與 AI 教練
      // 都不讀它，這裡純粹是「讓你看到手錶顯示了什麼」。因此不掛信賴度
      // badge（那是 fact 的語彙），改用專屬的弱化註記說明它只能對照參考。
      var gref = data.hrv.garmin_reference;
      if (gref && gref.status) {
        var statusRow = h('div', 'today-fact-row');
        statusRow.appendChild(h('span', 'today-fact-label', 'Garmin HRV 狀態'));
        var statusVal = h('span', 'today-fact-value', null);
        var statusText = h('span', HRV_STATUS_CLASS[gref.status] || '',
          HRV_STATUS_LABEL[gref.status] || gref.status);
        statusVal.appendChild(statusText);
        var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
        refBadge.title = gref.note || '';
        statusVal.appendChild(refBadge);
        statusRow.appendChild(statusVal);
        loadBlock.appendChild(statusRow);

        // 平衡區間對照的是 7 日均值、不是昨夜值——例如昨夜 77 ms 高於
        // 47–63 ms 仍判 UNBALANCED，是 7 日均值落在區間外
        if (gref.baseline_balanced_low && gref.baseline_balanced_upper) {
          var baseRow = h('div', 'today-fact-row');
          baseRow.appendChild(h('span', 'today-fact-label', 'Garmin 平衡區間（對照 7 日均值）'));
          baseRow.appendChild(h('span', 'today-fact-value',
            C.formatNumber(gref.baseline_balanced_low, 0) + '–'
            + C.formatNumber(gref.baseline_balanced_upper, 0) + ' ms'));
          loadBlock.appendChild(baseRow);
        }
      }

      if (data.hrv.delta_pct) {
        var deltaRow = h('div', 'today-fact-row');
        deltaRow.appendChild(h('span', 'today-fact-label', '昨夜較 7 日均值'));
        deltaRow.appendChild(factValueNode(data.hrv.delta_pct, function (v) { return fmtDelta(v, 1, '%'); }));
        loadBlock.appendChild(deltaRow);
      }
    }
  }
  grid.appendChild(loadBlock);

  // 最近一場活動卡
  var lastBlock = h('div', 'today-block');
  lastBlock.appendChild(h('div', 'today-block-title', '最近一場'));
  if (!data.last_activity) {
    lastBlock.appendChild(h('div', 'state-msg state-unavailable', '尚無活動紀錄'));
  } else {
    var la = data.last_activity;
    var btn = h('button', 'session-card session-card-inline', null);
    btn.type = 'button';
    var text = la.date + '　' + (ACTIVITY_TYPE_LABEL[la.activity_type] || la.activity_type);
    if (la.distance_km) text += '　' + C.formatNumber(la.distance_km, 1) + ' km';
    if (la.avg_pace_sec_per_km) text += '　' + C.formatPace(la.avg_pace_sec_per_km) + '/km';
    btn.textContent = text;
    if (la.activity_type && RUNNING_ACTIVITY_TYPES_CLIENT.indexOf(la.activity_type) !== -1) {
      btn.addEventListener('click', function () { navigate('sessions', la.id); });
    } else {
      btn.disabled = true;
      btn.title = '非跑步活動，無單場分析頁可跳轉';
    }
    lastBlock.appendChild(btn);
  }
  grid.appendChild(lastBlock);

  node.appendChild(grid);
}

/** 本週（週日起算，與行事曆一致）已完成的跑步距離與本週課表總距離。 */
export function weekVolumeSummary() {
  var days = state.weekDays || [];
  var done = 0;
  var planned = 0;
  days.forEach(function (d) {
    (d.activities || []).forEach(function (a) {
      if (a.is_running && a.distance_km) done += a.distance_km;
    });
    (d.planned || []).forEach(function (p) {
      if (p.planned_distance_km) planned += p.planned_distance_km;
    });
  });
  return { done: done, planned: planned };
}

export function renderKpiStrip() {
  var node = $('kpi-strip');
  if (!node) return;
  clear(node);

  // weekDays 與 todayKpi 是兩支不同的請求，任一支還沒回來就先顯示載入中，
  // 避免畫出一半的卡片再跳動
  if (!state.weekDays || !state.todayKpi) {
    showLoading(node, '載入近期概況');
    return;
  }

  var strip = h('div', 'kpi-strip');
  var kpi = state.todayKpi;

  // ---- 本週跑量進度
  var vol = weekVolumeSummary();
  var volCard = kpiCard({
    label: '本週跑量',
    value: C.formatNumber(vol.done, 1),
    unit: 'km',
    note: vol.planned > 0
      ? '課表 ' + C.formatNumber(vol.planned, 1) + ' km'
      : '本週無課表距離'
  });
  if (vol.planned > 0) {
    var pct = Math.min(100, (vol.done / vol.planned) * 100);
    var bar = h('div', 'kpi-bar');
    var fill = h('div', 'kpi-bar-fill');
    fill.style.width = pct.toFixed(1) + '%';
    bar.appendChild(fill);
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label',
      '本週跑量 ' + C.formatNumber(vol.done, 1) + ' km，課表 '
      + C.formatNumber(vol.planned, 1) + ' km，達成 ' + Math.round(pct) + '%');
    volCard.appendChild(bar);
    volCard.appendChild(h('div', 'kpi-note kpi-note-pct', '達成 ' + Math.round(pct) + '%'));
  }
  strip.appendChild(volCard);

  // ---- 本週跑步次數
  var runCount = 0;
  (state.weekDays || []).forEach(function (d) {
    (d.activities || []).forEach(function (a) { if (a.is_running) runCount += 1; });
  });
  strip.appendChild(kpiCard({
    label: '本週跑步次數',
    value: String(runCount),
    unit: '次',
    note: '週日起算'
  }));

  // ---- TSB（訓練壓力平衡）
  var loadSeries = kpi.loadSeries || [];
  var tsbVals = loadSeries.map(function (p) {
    return p.tsb && typeof p.tsb.value === 'number' ? p.tsb.value : null;
  }).filter(function (v) { return v !== null; });
  if (tsbVals.length > 0) {
    var lastTsb = tsbVals[tsbVals.length - 1];
    strip.appendChild(kpiCard({
      label: 'TSB（CTL − ATL）',
      value: (lastTsb > 0 ? '+' : '') + C.formatNumber(lastTsb, 1),
      note: '近 30 天走勢',
      series: tsbVals
    }));
  }

  // ---- HRV
  var hrvVals = (kpi.hrvPoints || []).map(function (p) { return p.value; })
    .filter(function (v) { return typeof v === 'number' && isFinite(v); });
  if (hrvVals.length > 0) {
    strip.appendChild(kpiCard({
      label: 'HRV 夜間均值',
      value: C.formatNumber(hrvVals[hrvVals.length - 1], 0),
      unit: 'ms',
      note: '近 30 天走勢',
      series: hrvVals
    }));
  }

  // ---- 靜止心率（越低越好，故 invert）
  var rhrVals = (kpi.rhrPoints || []).map(function (p) { return p.value; })
    .filter(function (v) { return typeof v === 'number' && isFinite(v); });
  if (rhrVals.length > 0) {
    strip.appendChild(kpiCard({
      label: '靜止心率',
      value: C.formatNumber(rhrVals[rhrVals.length - 1], 0),
      unit: 'bpm',
      note: '近 30 天走勢',
      series: rhrVals,
      invert: true
    }));
  }

  node.appendChild(strip);
}

/**
 * KPI 條要的資料散在兩支既有端點，這裡併發取回後一起渲染。
 * 固定用 30d：今日頁不吃頂列 range，迷你趨勢線也只需要「最近的走勢」。
 * 任一支失敗就當該卡沒資料（catch 回 null），不讓整條 KPI 消失。
 */
export function loadTodayKpi() {
  Promise.all([
    api('/api/training-load?range=30d').catch(function () { return null; }),
    api('/api/wellness-trend?range=30d').catch(function () { return null; })
  ]).then(function (res) {
    var load = res[0];
    var wellness = res[1];
    var metrics = (wellness && wellness.metrics) || {};
    state.todayKpi = {
      loadSeries: (load && load.series) || [],
      hrvPoints: (metrics.hrv_ms && metrics.hrv_ms.points) || [],
      rhrPoints: (metrics.resting_hr_bpm && metrics.resting_hr_bpm.points) || []
    };
    renderKpiStrip();
  });
}

// ------------------------------------------- 今日頁：本週課表一覽

export function renderWeekPlanList() {
  var node = $('week-plan-list');
  if (!node) return;
  clear(node);
  if (!state.weekDays) return;

  var list = h('div', 'week-plan-list');
  state.weekDays.forEach(function (day) {
    var row = h('div', 'wp-row day-' + day.day_state);
    row.appendChild(h('span', 'wp-dow', dowLabel(day.date)));
    row.appendChild(h('span', 'wp-date', day.date.slice(5)));

    var planText = '\u2014';
    if (day.planned && day.planned.length > 0) {
      var pl = day.planned[0];
      planText = WORKOUT_TYPE_LABEL[pl.workout_type] || pl.workout_type;
      if (pl.planned_distance_km) planText += ' ' + C.formatNumber(pl.planned_distance_km, 1) + ' km';
    }
    row.appendChild(h('span', 'wp-plan', planText));

    // 實際：把當天所有跑步距離加總，其餘類型只標類型名
    var runKm = 0;
    var otherTypes = [];
    (day.activities || []).forEach(function (a) {
      if (a.is_running && a.distance_km) runKm += a.distance_km;
      else if (!a.is_running) otherTypes.push(ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type);
    });
    var actParts = [];
    if (runKm > 0) actParts.push(C.formatNumber(runKm, 1) + ' km');
    if (otherTypes.length > 0) actParts.push(otherTypes.join('、'));
    row.appendChild(h('span', 'wp-actual', actParts.length > 0 ? actParts.join('　') : '\u2014'));

    row.appendChild(h('span', 'wp-state', DAY_STATE_LABEL[day.day_state] || day.day_state));
    list.appendChild(row);
  });
  node.appendChild(list);
}

// ------------------------------------------- 今日頁：最近訓練清單

export function renderRecentSessions() {
  var node = $('recent-sessions');
  if (!node) return;
  clear(node);
  if (!state.recentSessions) { showLoading(node, '載入最近訓練'); return; }
  if (state.recentSessions.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '近 30 天沒有跑步場次'));
    return;
  }

  var list = h('div', 'recent-list');
  state.recentSessions.slice(0, 8).forEach(function (s) {
    var row = h('button', 'recent-row', null);
    row.type = 'button';
    row.addEventListener('click', function () { navigate('sessions', s.id); });

    row.appendChild(h('span', 'rc-date', s.date.slice(5)));
    row.appendChild(h('span', 'rc-type', WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type || '未分類'));
    row.appendChild(h('span', 'rc-km', s.distance_km ? C.formatNumber(s.distance_km, 2) + ' km' : '\u2014'));
    row.appendChild(h('span', 'rc-pace', s.avg_pace_sec_per_km ? C.formatPace(s.avg_pace_sec_per_km) + '/km' : '\u2014'));
    row.appendChild(h('span', 'rc-hr', s.avg_hr_bpm ? s.avg_hr_bpm + ' bpm' : '\u2014'));
    list.appendChild(row);
  });
  node.appendChild(list);
}

export function loadRecentSessions() {
  api('/api/sessions?range=30d').then(function (data) {
    state.recentSessions = (data && data.sessions) || [];
    renderRecentSessions();
  }).catch(function (err) {
    showError($('recent-sessions'), err.message);
  });
}

export function loadToday() {
  showLoading($('today-body'), '載入今日狀態');
  api('/api/today').then(function (data) {
    state.today = data;
    renderToday();
    // 設定頁的「最後同步」與側邊列同源，資料回來時一併更新（設定頁此刻
    // 可能不在畫面上，但它沒有圖表、不吃容器寬度，直接寫入即可）
    renderSettingsSync();
  }).catch(function (err) {
    showError($('today-body'), err.message);
  });
}

export function renderWeekStrip() {
  var node = $('week-strip');
  clear(node);
  if (!state.weekDays) { showLoading(node, '載入本週'); return; }

  var grid = h('div', 'week-strip');
  state.weekDays.forEach(function (day) {
    var cell = h('div', 'week-cell day-' + day.day_state);
    var runnable = firstRunningActivity(day.activities);
    if (runnable) {
      cell.classList.add('is-clickable');
      cell.addEventListener('click', function () {
        navigate('sessions', runnable.id);
      });
    }
    cell.appendChild(h('span', 'week-cell-dow', dowLabel(day.date)));
    cell.appendChild(h('span', 'week-cell-date', day.date.slice(8, 10)));
    if (day.planned.length > 0) {
      var wt = day.planned[0].workout_type;
      cell.appendChild(h('span', 'week-cell-workout', WORKOUT_TYPE_LABEL[wt] || wt));
    }
    if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
    cell.title = day.date + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
    grid.appendChild(cell);
  });
  node.appendChild(grid);
}

export function loadWeekStrip() {
  var weekStart = sundayOf(todayISO());
  var start = weekStart.toISOString().slice(0, 10);
  var sunday = addDaysISO(start, 6); // 週日 + 6 天 = 週六，本週的最後一天
  api('/api/calendar?start=' + start + '&end=' + sunday).then(function (data) {
    state.weekDays = data.days || [];
    renderWeekStrip();
    // 本週課表一覽與 KPI 的「本週跑量」都吃同一份 weekDays，
    // 不另發請求，資料回來時一併更新
    renderWeekPlanList();
    renderKpiStrip();
  }).catch(function (err) {
    showError($('week-strip'), err.message);
  });
}
