/**
 * sessions.js — 訓練紀錄 view：場次清單、單場分析（分圈、紀錄、心率漂移、心率區間、隔天恢復）
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, SOURCE_LABEL } from '../core/state.js';
import { $, clear, h, showUnavailable, statCard, widthOf, wrapChart } from '../core/dom.js';
import { fmtDelta, deltaClass } from '../core/format.js';
import { selectSession } from '../shell/range.js';

export function renderSessionPicker() {
  var node = $('session-picker');
  clear(node);
  if (state.sessions.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步場次'));
    return;
  }
  var select = h('select', 'session-select');
  select.setAttribute('aria-label', '選擇要分析的場次');
  state.sessions.forEach(function (s) {
    var opt = document.createElement('option');
    opt.value = String(s.id);
    opt.textContent = s.date + '　' + C.formatNumber(s.distance_km, 2) + ' km　'
      + (s.workout_type ? (WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type) : '未標記');
    if (s.id === state.selectedSessionId) opt.selected = true;
    select.appendChild(opt);
  });
  select.addEventListener('change', function () {
    selectSession(parseInt(select.value, 10));
  });
  node.appendChild(select);
}

export function renderSessionSummary(detail) {
  var node = $('session-summary');
  clear(node);
  var s = detail.summary || {};

  var head = h('div', 'session-head');
  var titleWrap = h('div', 'session-title-wrap');
  titleWrap.appendChild(h('h3', 'session-title', detail.title || '（無標題）'));
  titleWrap.appendChild(h('div', 'session-meta',
    (detail.started_at || '').replace('T', ' ') + '　・　' + (detail.activity_type || '—')));
  head.appendChild(titleWrap);

  // 訓練類型標記（含 auto/manual 來源與計畫類型對照）
  var tags = h('div', 'tag-row');
  if (detail.workout_type) {
    var tag = h('span', 'tag tag-type tag-type-' + detail.workout_type,
      WORKOUT_TYPE_LABEL[detail.workout_type] || detail.workout_type);
    tags.appendChild(tag);
    if (detail.workout_type_source) {
      tags.appendChild(h('span', 'tag tag-source',
        SOURCE_LABEL[detail.workout_type_source] || detail.workout_type_source));
    }
  } else {
    tags.appendChild(h('span', 'tag tag-muted', '訓練類型：未標記'));
  }
  if (detail.planned) {
    var plannedText = '計畫：'
      + (detail.planned.workout_type
        ? (WORKOUT_TYPE_LABEL[detail.planned.workout_type] || detail.planned.workout_type)
        : '—');
    if (detail.planned.planned_distance_km !== null && detail.planned.planned_distance_km !== undefined) {
      plannedText += ' ' + C.formatNumber(detail.planned.planned_distance_km, 1) + ' km';
    }
    tags.appendChild(h('span', 'tag tag-planned', plannedText));
  } else {
    tags.appendChild(h('span', 'tag tag-muted', '無對應計畫'));
  }
  head.appendChild(tags);
  node.appendChild(head);

  var grid = h('div', 'stat-grid');
  grid.appendChild(statCard('距離', C.formatNumber(s.distance_km, 2), 'km'));
  grid.appendChild(statCard('時間', C.formatDuration(s.duration_sec), ''));
  grid.appendChild(statCard('平均配速', C.formatPace(s.avg_pace_sec_per_km), '/km'));
  grid.appendChild(statCard('平均心率', C.formatNumber(s.avg_hr_bpm, 0), 'bpm'));
  grid.appendChild(statCard('最大心率', C.formatNumber(s.max_hr_bpm, 0), 'bpm'));
  grid.appendChild(statCard('平均步頻', C.formatNumber(s.avg_cadence_spm, 0), 'spm'));
  grid.appendChild(statCard('有氧 TE', C.formatNumber(s.aerobic_te, 1), ''));
  grid.appendChild(statCard('爬升', C.formatNumber(s.elevation_gain_m, 0), 'm'));
  grid.appendChild(statCard('熱量', C.formatNumber(s.calories, 0), 'kcal'));
  node.appendChild(grid);
}

export function renderHrDrift(detail) {
  var node = $('hr-drift');
  clear(node);
  var drift = detail.hr_drift;
  if (!drift || drift.available === false) {
    showUnavailable(node, drift && drift.reason ? drift.reason : '無心率漂移資料');
    return;
  }
  var grid = h('div', 'stat-grid stat-grid-3');
  grid.appendChild(statCard('前半段平均心率', C.formatNumber(drift.first_half_avg_hr, 0), 'bpm'));
  grid.appendChild(statCard('後半段平均心率', C.formatNumber(drift.second_half_avg_hr, 0), 'bpm'));
  grid.appendChild(statCard('心率漂移', C.formatNumber(drift.drift_pct, 1) + '%', ''));
  node.appendChild(grid);
}

export function renderLaps(detail) {
  var node = $('lap-chart');
  clear(node);
  var laps = detail.laps;
  if (!laps || laps.available === false) {
    showUnavailable(node, laps && laps.reason ? laps.reason : '此活動無分圈資料');
    return;
  }
  var list = laps.laps || [];
  if (list.length === 0) {
    showUnavailable(node, '此活動無分圈資料');
    return;
  }
  if (laps.source) {
    var src = h('div', 'chart-note',
      '資料來源：' + (laps.source === 'fit' ? 'FIT 每公里分圈' : '裝置手動分圈'));
    node.appendChild(src);
  }
  node.appendChild(wrapChart(C.lapPaceChart(list, { containerWidth: widthOf(node) })));
}

export function renderRecords(detail) {
  var node = $('records-chart');
  clear(node);
  var records = detail.records;
  if (!records || records.available === false) {
    showUnavailable(node, records && records.reason ? records.reason : '此活動尚未解析逐秒資料');
    return;
  }
  var points = records.points || [];
  if (points.length === 0) {
    showUnavailable(node, '此活動尚未解析逐秒資料');
    return;
  }
  var legend = h('div', 'legend');
  var l1 = h('span', 'legend-item');
  l1.appendChild(h('span', 'swatch swatch-pace'));
  l1.appendChild(h('span', null, '配速（左軸，上快下慢）'));
  var l2 = h('span', 'legend-item');
  l2.appendChild(h('span', 'swatch swatch-hr'));
  l2.appendChild(h('span', null, '心率（右軸）'));
  legend.appendChild(l1);
  legend.appendChild(l2);
  node.appendChild(legend);
  if (records.sample_every_sec) {
    node.appendChild(h('div', 'chart-note', '取樣間隔：每 ' + records.sample_every_sec + ' 秒一筆'));
  }
  node.appendChild(wrapChart(C.recordsChart(points, { containerWidth: widthOf(node) })));
}

/**
 * 心率區間分布。zones.zones 固定回傳 Z1~Z5 共 5 列（後端保證，即使某場
 * 沒進過某區間也是 seconds=0 照樣列出）——跨場次比較時圖表軸線才會一致，
 * 不因某場強度較低就少幾列。below_zone_1（低於 Z1 的暖身時間）同樣固定
 * 顯示在圖表下方，沒有暖身時間就顯示 0，而不是整行隱藏。
 */
export function renderHrZones(detail) {
  var node = $('hrzone-chart');
  clear(node);
  var zones = detail.hr_zones;
  if (!zones || zones.available === false) {
    showUnavailable(node, zones && zones.reason ? zones.reason : '此活動無心率區間資料');
    return;
  }
  node.appendChild(wrapChart(C.hrZoneChart(zones.zones, { containerWidth: widthOf(node) })));
  var below = zones.below_zone_1;
  if (below) {
    node.appendChild(h('div', 'chart-note',
      '低於 Zone 1（暖身）：' + C.formatDuration(below.seconds)
      + '（' + below.pct.toFixed(0) + '%）'));
  }
}

/** 該場的隔天恢復影響：只顯示數字卡，不畫圖、不做解讀 */
export function renderRecoveryImpact() {
  var node = $('recovery-impact');
  clear(node);
  if (state.selectedSessionId === null) {
    showUnavailable(node, '尚未選擇場次');
    return;
  }
  var impact = null;
  for (var i = 0; i < state.recoveryImpacts.length; i += 1) {
    if (state.recoveryImpacts[i].activity_id === state.selectedSessionId) {
      impact = state.recoveryImpacts[i];
      break;
    }
  }
  if (!impact) {
    showUnavailable(node, '目前區間內沒有這場的恢復關聯資料');
    return;
  }
  var nextDay = impact.next_day;
  if (!nextDay || nextDay.available === false) {
    showUnavailable(node, (nextDay && nextDay.reason)
      ? nextDay.reason
      : '隔天沒有身體狀況資料');
    return;
  }
  node.appendChild(h('div', 'chart-note',
    '訓練日 ' + impact.training_date + ' → 隔天 ' + nextDay.date + '（數值為隔天減訓練當天）'));

  var grid = h('div', 'stat-grid stat-grid-3');
  var hrvCard = statCard('HRV 夜間均值變化',
    fmtDelta(nextDay.hrv_delta, 1, ' ms'),
    nextDay.hrv_delta_pct === null || nextDay.hrv_delta_pct === undefined
      ? '' : fmtDelta(nextDay.hrv_delta_pct, 1, '%'));
  hrvCard.className += deltaClass(nextDay.hrv_delta);
  grid.appendChild(hrvCard);

  var rhrCard = statCard('靜止心率變化', fmtDelta(nextDay.resting_hr_delta, 0, ' bpm'), '');
  rhrCard.className += deltaClass(nextDay.resting_hr_delta);
  grid.appendChild(rhrCard);

  var readyCard = statCard('訓練準備度變化', fmtDelta(nextDay.training_readiness_delta, 0, ''), '');
  readyCard.className += deltaClass(nextDay.training_readiness_delta);
  grid.appendChild(readyCard);

  node.appendChild(grid);
}

export function renderSessionDetail() {
  var detail = state.sessionDetail;
  if (!detail) return;
  renderSessionSummary(detail);
  renderLaps(detail);
  renderRecords(detail);
  renderHrDrift(detail);
  renderHrZones(detail);
  renderRecoveryImpact();
}

/**
 * 場次列表：桌機為表格，手機（<768px）由 CSS 改為卡片式堆疊，
 * 不使用橫向捲動的多欄表格。
 */
export function renderSessionList() {
  var node = $('session-list');
  clear(node);
  if (state.sessions.length === 0) return;

  node.appendChild(h('h3', 'chart-title', '場次列表'));
  var list = h('div', 'session-cards');
  state.sessions.forEach(function (s) {
    var row = h('button', 'session-card' + (s.id === state.selectedSessionId ? ' is-selected' : ''));
    row.type = 'button';
    row.setAttribute('aria-label', '檢視 ' + s.date + ' 的場次');

    var c1 = h('span', 'sc-cell sc-date');
    c1.appendChild(h('span', 'sc-key', '日期'));
    c1.appendChild(h('span', 'sc-val', s.date));

    var c2 = h('span', 'sc-cell');
    c2.appendChild(h('span', 'sc-key', '類型'));
    c2.appendChild(h('span', 'sc-val',
      s.workout_type ? (WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type) : '未標記'));

    var c3 = h('span', 'sc-cell');
    c3.appendChild(h('span', 'sc-key', '距離'));
    c3.appendChild(h('span', 'sc-val', C.formatNumber(s.distance_km, 2) + ' km'));

    var c4 = h('span', 'sc-cell');
    c4.appendChild(h('span', 'sc-key', '配速'));
    c4.appendChild(h('span', 'sc-val', C.formatPace(s.avg_pace_sec_per_km) + '/km'));

    var c5 = h('span', 'sc-cell');
    c5.appendChild(h('span', 'sc-key', '平均心率'));
    c5.appendChild(h('span', 'sc-val',
      s.avg_hr_bpm === null || s.avg_hr_bpm === undefined ? '—' : s.avg_hr_bpm + ' bpm'));

    var c6 = h('span', 'sc-cell');
    c6.appendChild(h('span', 'sc-key', '時間'));
    c6.appendChild(h('span', 'sc-val', C.formatDuration(s.duration_sec)));

    [c1, c2, c3, c4, c5, c6].forEach(function (c) { row.appendChild(c); });
    row.addEventListener('click', function () { selectSession(s.id); });
    list.appendChild(row);
  });
  node.appendChild(list);
}
