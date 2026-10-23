/**
 * sessions.js — 訓練紀錄 view：按週分組的日誌列清單（E.3 C1，點列原地展開）、
 * 單場分析元件（分圈、紀錄、心率漂移、心率區間、隔天恢復；展開列與完整單場共用）
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, SOURCE_LABEL } from '../core/state.js';
import { $, clear, h, showError, showLoading, showUnavailable, statCard, widthOf, wrapChart } from '../core/dom.js';
import { fmtDelta, deltaClass, mondayOf, addDaysISO, dowLabel } from '../core/format.js';
import { selectSession } from '../shell/range.js';
import { navigate } from '../shell/router.js';

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
  // 這個下拉選單在「完整單場」畫面裡，換場後留在完整單場
  select.addEventListener('change', function () {
    selectSession(parseInt(select.value, 10), true);
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

export function renderLaps(detail, target) {
  var node = target || $('lap-chart');
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

export function renderRecords(detail, target) {
  var node = target || $('records-chart');
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
export function renderHrZones(detail, target) {
  var node = target || $('hrzone-chart');
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
export function renderRecoveryImpact(target) {
  var node = target || $('recovery-impact');
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

// ------------------------------------------------------------ E.3 訓練紀錄：C1 日誌列
//
// 清單按週（週一～週日）分組；點一列在原地展開（大數字列＋圖表／分圈／心率區間／
// 隔天恢復分頁），展開內容沿用上方的單場元件。展開哪一列由網址決定：
//   #/sessions           全部收合
//   #/sessions/<id>      清單、該列展開並捲到畫面內
//   #/sessions/<id>/full 「完整單場」（#panel-session，所有單場元件一次列出）
// 狀態：state.selectedSessionId＝展開中的場次（null＝全部收合）。

// 課種篩選。品質課＝節奏跑＋間歇跑；輕鬆跑、恢復跑、未分類只在「全部」出現。
var TYPE_FILTERS = [
  { key: 'all', label: '全部', types: null },
  { key: 'quality', label: '品質課', types: ['tempo', 'interval'] },
  { key: 'lsd', label: 'LSD', types: ['lsd'] },
  { key: 'race', label: '比賽', types: ['race'] }
];
var typeFilter = 'all';

// 展開列的分頁；換列時沿用（看完一場的分圈，點下一場仍停在分圈）
var EXPAND_TABS = [
  { key: 'chart', label: '圖表' },
  { key: 'laps', label: '分圈' },
  { key: 'zones', label: '心率區間' },
  { key: 'recovery', label: '隔天恢復' }
];
var expandTab = 'chart';

// 單場明細讀取失敗：{ id, message }，展開列的分頁顯示錯誤而不是一直轉圈
var detailError = null;

function filterDef(key) {
  for (var i = 0; i < TYPE_FILTERS.length; i += 1) {
    if (TYPE_FILTERS[i].key === key) return TYPE_FILTERS[i];
  }
  return TYPE_FILTERS[0];
}

function matchesFilter(s, key) {
  var types = filterDef(key).types;
  return types === null || types.indexOf(s.workout_type) !== -1;
}

function typeKey(s) { return s.workout_type || 'unknown'; }

function typeLabel(s) {
  return s.workout_type ? (WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type) : '未標記';
}

/** 「完整單場」畫面是否開著（router.updateSessionsStage 切換 .stage-detail） */
function isFullMode() {
  var view = $('view-sessions');
  return !!view && view.classList.contains('stage-detail');
}

/** 依所在週的週一分組；sessions 已由新到舊，分組後順序不變 */
function groupByWeek(sessions) {
  var weeks = [];
  var byStart = {};
  sessions.forEach(function (s) {
    var start = mondayOf(s.date).toISOString().slice(0, 10);
    if (!byStart[start]) {
      byStart[start] = { start: start, sessions: [] };
      weeks.push(byStart[start]);
    }
    byStart[start].sessions.push(s);
  });
  return weeks;
}

/** 週標題列：日期區間＋場數・距離・時間（距離與時間含併入主課的暖身，與行事曆合計一致） */
function weekHeader(week) {
  var dist = 0;
  var sec = 0;
  var hasWarmup = false;
  week.sessions.forEach(function (s) {
    dist += s.distance_km || 0;
    sec += s.duration_sec || 0;
    if (s.warmup) {
      hasWarmup = true;
      dist += s.warmup.distance_km || 0;
      sec += s.warmup.duration_sec || 0;
    }
  });
  var head = h('div', 'slog-week');
  head.setAttribute('role', 'heading');
  head.setAttribute('aria-level', '3');
  head.appendChild(h('span', 'slog-week-range',
    C.shortDate(week.start) + ' – ' + C.shortDate(addDaysISO(week.start, 6))));
  var totals = h('span', 'slog-week-totals',
    week.sessions.length + ' 場 · ' + C.formatNumber(dist, 1) + ' km · ' + C.formatDuration(sec));
  if (hasWarmup) totals.title = '距離與時間含併入主課的暖身';
  head.appendChild(totals);
  return head;
}

function numCell(cls, value, unit) {
  var cell = h('span', 'slog-num ' + cls);
  cell.appendChild(h('span', 'slog-num-val', value));
  cell.appendChild(h('span', 'slog-num-unit', unit));
  return cell;
}

/** 分圈小圖；沒有 FIT 分圈（lap_strip 為 []）時顯示淡淡的佔位 */
function lapStripCell(s) {
  var cell = h('span', 'slog-strip');
  var laps = s.lap_strip || [];
  if (laps.length === 0) {
    cell.classList.add('is-empty');
    cell.title = '此場沒有 FIT 分圈';
    cell.appendChild(h('span', 'slog-strip-none', '無分圈'));
    return cell;
  }
  // 圈數少時不把柱子拉得很寬：容器最寬 = 圈數 × 固定寬（見 CSS --slog-laps）
  cell.style.setProperty('--slog-laps', String(laps.length));
  cell.appendChild(C.lapStrip(laps, {
    height: 28,
    tooltip: true,
    label: laps.length + ' 圈分圈小圖：高度為配速，顏色為該圈平均心率區間'
  }));
  return cell;
}

function sessionRow(s, expanded) {
  var item = h('div', 'slog-item slog-wt-' + typeKey(s) + (expanded ? ' is-expanded' : ''));
  item.setAttribute('data-session-id', String(s.id));

  var row = h('button', 'slog-row');
  row.type = 'button';
  row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  row.setAttribute('aria-controls', 'slog-expand-' + s.id);

  row.appendChild(h('span', 'slog-stripe'));

  var date = h('span', 'slog-date');
  date.appendChild(h('span', 'slog-date-md', C.shortDate(s.date)));
  date.appendChild(h('span', 'slog-date-dow', '週' + dowLabel(s.date)));
  row.appendChild(date);

  var what = h('span', 'slog-what');
  what.appendChild(h('span', 'tag tag-type tag-type-' + typeKey(s) + ' slog-type', typeLabel(s)));
  var sub = h('span', 'slog-sub');
  sub.appendChild(h('span', 'slog-title', s.title || '（無標題）'));
  if (s.warmup) {
    sub.appendChild(h('span', 'slog-warmup', '＋暖身 ' + C.formatNumber(s.warmup.distance_km || 0, 1) + ' km'));
  }
  what.appendChild(sub);
  row.appendChild(what);

  row.appendChild(numCell('slog-dist', C.formatNumber(s.distance_km, 2), 'km'));
  row.appendChild(numCell('slog-time', C.formatDuration(s.duration_sec), '時間'));
  row.appendChild(numCell('slog-pace', C.formatPace(s.avg_pace_sec_per_km), '/km'));
  row.appendChild(lapStripCell(s));

  var chevron = h('span', 'slog-chevron', '▾');
  chevron.setAttribute('aria-hidden', 'true');
  row.appendChild(chevron);

  row.addEventListener('click', function () {
    if (expanded) navigate('sessions'); // 再點一次收合
    else selectSession(s.id);
  });
  item.appendChild(row);

  if (expanded) item.appendChild(expandBody(s));
  return item;
}

function bigNumber(label, value, unit) {
  var item = h('div', 'slog-big-item');
  var val = h('span', 'slog-big-val', value);
  if (unit && value !== '—') val.appendChild(h('span', 'slog-big-unit', unit));
  item.appendChild(val);
  item.appendChild(h('span', 'slog-big-label', label));
  return item;
}

/** 展開區：大數字列（清單資料，不必等明細）＋分頁＋「開啟完整單場」 */
function expandBody(s) {
  var body = h('div', 'slog-expand');
  body.id = 'slog-expand-' + s.id;

  var top = h('div', 'slog-big-row');
  var big = h('div', 'slog-big');
  big.appendChild(bigNumber('距離', C.formatNumber(s.distance_km, 2), 'km'));
  big.appendChild(bigNumber('時間', C.formatDuration(s.duration_sec), ''));
  big.appendChild(bigNumber('平均配速', C.formatPace(s.avg_pace_sec_per_km), '/km'));
  big.appendChild(bigNumber('平均心率', C.formatNumber(s.avg_hr_bpm, 0), 'bpm'));
  big.appendChild(bigNumber('有氧 TE', C.formatNumber(s.aerobic_te, 1), ''));
  top.appendChild(big);
  var link = h('a', 'slog-full-link', '開啟完整單場 ›');
  link.href = '#/sessions/' + s.id + '/full';
  top.appendChild(link);
  body.appendChild(top);

  if (s.warmup) {
    var w = s.warmup;
    var parts = [C.formatNumber(w.distance_km || 0, 2) + ' km', C.formatDuration(w.duration_sec)];
    if (w.avg_pace_sec_per_km) parts.push(C.formatPace(w.avg_pace_sec_per_km) + '/km');
    body.appendChild(h('div', 'chart-note slog-warmup-note',
      '暖身另計：' + parts.join('・') + '（另存的獨立活動，不含在上方數字內）'));
  }

  var tabs = h('div', 'slog-tabs');
  tabs.setAttribute('role', 'tablist');
  var panel = h('div', 'slog-tab-panel');
  panel.setAttribute('role', 'tabpanel');
  EXPAND_TABS.forEach(function (tab) {
    var btn = h('button', 'slog-tab' + (tab.key === expandTab ? ' is-active' : ''), tab.label);
    btn.type = 'button';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', tab.key === expandTab ? 'true' : 'false');
    btn.addEventListener('click', function () {
      expandTab = tab.key;
      Array.prototype.forEach.call(tabs.children, function (b, i) {
        var on = EXPAND_TABS[i].key === expandTab;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      renderExpandPanel(panel);
    });
    tabs.appendChild(btn);
  });
  body.appendChild(tabs);
  body.appendChild(panel);
  return body;
}

/** 依目前分頁畫展開區內容。必須在 panel 已插入 DOM 後呼叫（圖表要量容器寬）。 */
function renderExpandPanel(panel) {
  if (expandTab === 'recovery') { // 恢復資料來自 /api/recovery-impact，不必等明細
    renderRecoveryImpact(panel);
    return;
  }
  var detail = state.sessionDetail;
  if (!detail || detail.id !== state.selectedSessionId) {
    if (detailError && detailError.id === state.selectedSessionId) showError(panel, detailError.message);
    else showLoading(panel, '載入單場資料');
    return;
  }
  if (expandTab === 'chart') {
    renderRecords(detail, panel);
    // 沒有逐秒資料（近期活動常見）時，圖表分頁不留一片空白：說明下方接著畫分圈配速
    var hasRecords = detail.records && detail.records.available !== false
      && (detail.records.points || []).length > 0;
    var hasLaps = detail.laps && detail.laps.available !== false && (detail.laps.laps || []).length > 0;
    if (!hasRecords && hasLaps) {
      var lapBox = h('div', 'slog-chart-fallback');
      panel.appendChild(lapBox);
      renderLaps(detail, lapBox);
    }
  } else if (expandTab === 'laps') renderLaps(detail, panel);
  else renderHrZones(detail, panel);
}

function renderTypeFilters() {
  var node = $('session-filters');
  if (!node) return;
  var bar = h('div', 'slog-filters');
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', '課種篩選');
  TYPE_FILTERS.forEach(function (f) {
    var count = state.sessions.filter(function (s) { return matchesFilter(s, f.key); }).length;
    var btn = h('button', 'slog-filter' + (f.key === typeFilter ? ' is-active' : ''));
    btn.type = 'button';
    btn.setAttribute('aria-pressed', f.key === typeFilter ? 'true' : 'false');
    btn.appendChild(h('span', null, f.label));
    btn.appendChild(h('span', 'slog-filter-count', String(count)));
    btn.addEventListener('click', function () {
      if (typeFilter === f.key) return;
      typeFilter = f.key;
      renderSessionList();
    });
    bar.appendChild(btn);
  });
  node.replaceChildren(bar);
}

/**
 * 訓練紀錄清單（C1 日誌列）。整份清單先在記憶體建好再一次替換，
 * 不先清空——清空的瞬間頁面變矮，捲動位置會被瀏覽器夾回頂端。
 */
export function renderSessionList() {
  var node = $('session-list');
  if (!node) return;
  renderTypeFilters();

  var wrap = h('div', 'slog');
  if (!state.sessionsReady) {
    wrap.appendChild(h('div', 'state-msg state-loading', '載入場次…'));
    node.replaceChildren(wrap);
    return;
  }
  if (state.sessions.length === 0) {
    wrap.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步場次'));
    node.replaceChildren(wrap);
    return;
  }

  var shown = state.sessions.filter(function (s) { return matchesFilter(s, typeFilter); });
  var selected = state.selectedSessionId;
  if (selected !== null && !shown.some(function (s) { return s.id === selected; })) {
    // 深連結到區間外（或被篩選掉）的場次：清單沒有這列可以展開，給一個直達完整單場的入口
    var miss = h('div', 'slog-miss');
    miss.appendChild(h('span', null, '選取的場次不在目前的區間或篩選內。'));
    var link = h('a', 'slog-full-link', '開啟完整單場 ›');
    link.href = '#/sessions/' + selected + '/full';
    miss.appendChild(link);
    wrap.appendChild(miss);
  }

  if (shown.length === 0) {
    wrap.appendChild(h('div', 'state-msg state-unavailable',
      '此區間內沒有「' + filterDef(typeFilter).label + '」場次'));
  }
  groupByWeek(shown).forEach(function (week) {
    var group = h('section', 'slog-group');
    group.appendChild(weekHeader(week));
    week.sessions.forEach(function (s) {
      group.appendChild(sessionRow(s, s.id === selected));
    });
    wrap.appendChild(group);
  });
  node.replaceChildren(wrap);

  var item = node.querySelector('.slog-item.is-expanded');
  if (item) renderExpandPanel(item.querySelector('.slog-tab-panel'));
  scrollExpandedIntoView();
}

/**
 * 依 state.sessionScroll 把展開列捲進畫面（一次性）。清單還沒載入時保留
 * 這個要求，等清單畫出來再捲；清單已載入但沒有這列（區間外）就放棄。
 */
export function scrollExpandedIntoView() {
  if (!state.sessionScroll) return;
  var item = document.querySelector('#session-list .slog-item.is-expanded');
  if (item && item.offsetParent !== null) {
    item.scrollIntoView({ block: state.sessionScroll });
    state.sessionScroll = null;
  } else if (state.sessionsReady) {
    state.sessionScroll = null;
  }
}

/** 明細回來：更新展開列的分頁與（開著時的）完整單場 */
export function renderSessionDetail() {
  var detail = state.sessionDetail;
  if (!detail) return;
  detailError = null;
  var panel = document.querySelector('#session-list .slog-item.is-expanded .slog-tab-panel');
  if (panel) renderExpandPanel(panel);
  renderSessionFull();
}

/** 完整單場（#/sessions/<id>/full）：沿用原本所有單場元件。畫面沒開時不畫（容器寬為 0）。 */
export function renderSessionFull() {
  var detail = state.sessionDetail;
  if (!detail || !isFullMode()) return;
  renderSessionPicker();
  renderSessionSummary(detail);
  renderLaps(detail);
  renderRecords(detail);
  renderHrDrift(detail);
  renderHrZones(detail);
  renderRecoveryImpact();
}

/** 明細讀取失敗：展開列顯示錯誤；完整單場的摘要區也顯示（不清掉其他區塊的骨架） */
export function renderSessionDetailError(id, message) {
  detailError = { id: id, message: message };
  var panel = document.querySelector('#session-list .slog-item.is-expanded .slog-tab-panel');
  if (panel && id === state.selectedSessionId) showError(panel, message);
  var summary = $('session-summary');
  if (summary) showError(summary, message);
}

