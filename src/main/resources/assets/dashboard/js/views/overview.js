/**
 * overview.js — 趨勢頁頂端的區間摘要（原總覽 view 中會隨時間範圍變動的部分）：
 *               摘要列、近 N 週跑量、每週跑量（強度堆疊）、心率區間分佈
 *
 * 2026-10-21 總覽與今日合併為首頁後，VDOT、個人最佳成績、本週 Zone 2、訓練負荷
 * 這些「現在」的數字移到首頁（views/home.js），這裡只留下吃時間範圍的區塊。
 */

import * as C from '../charts.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError, showUnavailable, widthOf, wrapChart } from '../core/dom.js';
import { infoTip, closeInfoPopover, kpiCard } from '../core/facts.js';

import { renderOrDefer } from '../shell/router.js';

// 資料來自 /api/overview（吃頂列 range）。每個區塊標出時間範圍（window-chip），
// ⓘ 只寫定義與算法，不寫判讀（判讀屬 AI 教練面板）。
// 增減幅度等標籤一律用中性樣式，不用紅綠暗示好壞。

// 強度分層的顯示資訊。分層本身（哪些 zone 算低／中／高）以後端回傳的
// weekly_intensity.tiers 為準，這裡只決定顏色與名稱。
export var INTENSITY_TIERS = [
  { key: 'low', name: '低強度', className: 'bar-intensity-low' },
  { key: 'mid', name: '中強度', className: 'bar-intensity-mid' },
  { key: 'high', name: '高強度', className: 'bar-intensity-high' },
  { key: 'unknown', name: '未分類', className: 'bar-intensity-unknown' }
];

export var OVERVIEW_NODE_IDS = ['overview-strip', 'overview-kpis', 'overview-volume', 'overview-zones'];

export function zoneListText(zones) {
  return zones.map(function (z) { return z === 0 ? '低於 Z1' : 'Z' + z; }).join(' + ');
}

/** 帶時間範圍標籤與 ⓘ 的面板標題列。 */
export function overviewHead(title, chip, tipText) {
  var head = h('div', 'overview-head');
  var t = h('h3', 'overview-title', title);
  if (chip) t.appendChild(h('span', 'window-chip', chip));
  head.appendChild(t);
  head.appendChild(infoTip(title, tipText));
  return head;
}

/** 百分比變化的中性標籤：+12% / −38%（前值為 0 時不算百分比，只標「前期 0」）。 */
export function changeLabel(current, previous, suffix) {
  if (!previous) return '前期 0 km';
  var pct = (current - previous) / previous * 100;
  return (pct >= 0 ? '+' : '−') + Math.abs(pct).toFixed(0) + '% ' + suffix;
}

/** range 系列卡片的時間標籤：實際起訖日（range=all 時沒有起日）。 */
export function rangeChip(ov) {
  return ov.start_date
    ? C.shortDate(ov.start_date) + '–' + C.shortDate(ov.end_date)
    : '全部（至 ' + C.shortDate(ov.end_date) + '）';
}

export function avgFullWeeks(weeks) {
  var full = weeks.filter(function (w) { return !w.partial; });
  if (full.length === 0) return null;
  return full.reduce(function (s, w) { return s + w.total_km; }, 0) / full.length;
}

// ---- 摘要列

export function renderOverviewStrip(ov) {
  var node = $('overview-strip');
  clear(node);
  var s = ov.sessions;
  var c = s.criteria;
  var weekAvg = avgFullWeeks(ov.weekly_intensity.weeks);
  var strip = h('div', 'overview-strip');

  function cell(title, chip, parts, tipText) {
    var box = h('div', 'strip-cell');
    var head = h('div', 'strip-title', title);
    if (chip) head.appendChild(h('span', 'window-chip', chip));
    if (tipText) head.appendChild(infoTip(title, tipText));
    box.appendChild(head);
    var line = h('div', 'strip-line');
    parts.forEach(function (p, i) {
      if (i > 0) line.appendChild(document.createTextNode(' · '));
      if (Array.isArray(p)) {
        line.appendChild(document.createTextNode(p[0]));
        line.appendChild(h('strong', null, p[1]));
        if (p[2]) line.appendChild(document.createTextNode(p[2]));
      } else {
        line.appendChild(document.createTextNode(p));
      }
    });
    box.appendChild(line);
    return box;
  }

  var summary = cell('摘要', rangeChip(ov), [
    ['跑步 ', String(s.total), ' 場'],
    ['', C.formatNumber(s.total_km, 1), ' km'],
    ['', C.formatDuration(s.total_sec)],
    weekAvg === null ? '無完整週' : ['完整週平均 ', C.formatNumber(weekAvg, 1), ' km']
  ]);
  summary.classList.add('strip-summary');
  strip.appendChild(summary);
  strip.appendChild(cell('資料品質', rangeChip(ov), [
    ['有效訓練 ', s.effective + '/' + s.total],
    ['心率區間 ', (ov.hr_zones.available ? ov.hr_zones.sessions_counted : 0) + '/' + s.total, ' 場']
  ], [
    '有效訓練：時間 ≥ ' + Math.round(c.min_duration_sec / 60) + ' 分鐘、距離 ≥ ' + c.min_distance_km + ' km'
      + (c.requires_hr ? '、且有心率資料' : '') + '的跑步。用來排除手錶誤觸、起跑就停、沒戴心率的紀錄；是資料品質篩選，不代表練得好不好。',
    '心率區間：有手錶心率區間停留時間的場次，下方強度堆疊與區間分佈只算得到這些場次。'
  ]));
  node.appendChild(strip);
}

// ---- 指標卡

export function recentVolumeCard(r, weeks) {
  // 走勢線：區間內每週總跑量（最後一週可能未完整，仍畫出來讓「現在」看得見）
  var series = weeks.map(function (w) { return w.total_km; });
  return kpiCard({
    label: '近 ' + (r.days / 7) + ' 週跑量',
    chip: C.shortDate(r.start) + '–' + C.shortDate(r.end),
    value: C.formatNumber(r.km, 1),
    unit: 'km',
    badge: h('span', 'neutral-badge', changeLabel(r.km, r.previous_km, 'vs 前 ' + (r.days / 7) + ' 週')),
    note: '前 ' + (r.days / 7) + ' 週 ' + C.formatNumber(r.previous_km, 1) + ' km（'
      + C.shortDate(r.previous_start) + '–' + C.shortDate(r.previous_end) + '）',
    series: series.length > 1 ? series : null,
    tip: [
      '最近 ' + r.days + ' 天（含今天）的跑步距離，與再往前 ' + r.days + ' 天對照。以今天為準滾動計算，不受上方時間區間影響。',
      '走勢線是上方時間區間內的每週跑量。'
    ]
  });
}

/** 近 N 週跑量卡（滾動計算、不受時間範圍影響；走勢線是時間範圍內的每週跑量）。 */
export function renderOverviewRecent(ov) {
  var node = $('overview-kpis');
  clear(node);
  var strip = h('div', 'kpi-strip overview-kpi-strip');
  strip.appendChild(recentVolumeCard(ov.recent_volume, ov.weekly_intensity.weeks));
  node.appendChild(strip);
}

// ---- 週跑量（強度堆疊）

export function renderOverviewVolume(ov) {
  var node = $('overview-volume');
  clear(node);
  var wi = ov.weekly_intensity;
  var tiers = wi.tiers || {};
  var head = overviewHead('每週跑量（依強度堆疊）', rangeChip(ov) + ' · 週一起算', [
    '每週的跑步距離，依每場心率區間停留時間的比例拆分：'
      + INTENSITY_TIERS.filter(function (t) { return tiers[t.key]; }).map(function (t) {
        return t.name + ' = ' + zoneListText(tiers[t.key]);
      }).join('；') + '。',
    '區間是手錶上設定的 Garmin %HRR 五區。沒有心率區間資料的場次整場列為「未分類」，不猜強度。',
    '淡色長條是被時間區間切到、或還沒過完的週；虛線是本週課表距離。'
  ]);
  node.appendChild(head);

  if (wi.weeks.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步'));
    return;
  }
  var thisWeekIdx = -1;
  var bars = wi.weeks.map(function (wk, i) {
    if (wk.week_start === ov.this_week.week_start) thisWeekIdx = i;
    return {
      label: C.shortDate(wk.week_start),
      weekStart: wk.week_start,
      partial: wk.partial,
      tipSuffix: wk.partial ? '（不完整週）' : '',
      segments: INTENSITY_TIERS.map(function (t) {
        return { value: wk[t.key + '_km'], className: t.className, name: t.name };
      })
    };
  });
  var targets = [];
  if (thisWeekIdx >= 0 && ov.this_week.planned_km) {
    targets.push({ index: thisWeekIdx, value: ov.this_week.planned_km,
                   label: '課表 ' + C.formatNumber(ov.this_week.planned_km, 1) });
  }
  node.appendChild(wrapChart(C.barChart(bars, {
    label: '每週跑量依強度堆疊長條圖',
    formatter: function (v) { return C.formatNumber(v, 1); },
    containerWidth: widthOf(node),
    targets: targets
  })));
  node.appendChild(intensityLegend());
}

export function intensityLegend() {
  var legend = h('div', 'legend overview-legend');
  INTENSITY_TIERS.forEach(function (t) {
    var item = h('span', 'legend-item');
    item.appendChild(h('span', 'swatch swatch-' + t.className));
    item.appendChild(h('span', null, t.name));
    legend.appendChild(item);
  });
  return legend;
}

// ---- 心率區間

/**
 * 低／中／高三段比例條，80% 處畫參考線（80/20 只是參考位置，不是判讀）。
 * @param {Array} zones [{zone, pct}]，pct 分母為 Z1~Z5
 * @param {boolean} compact 指標卡內用的細版
 */
export function zoneSplitBar(zones, compact) {
  var pct = {};
  zones.forEach(function (z) { pct[z.zone] = z.pct || 0; });
  var parts = [
    { cls: 'bar-intensity-low', v: (pct[1] || 0) + (pct[2] || 0) },
    { cls: 'bar-intensity-mid', v: pct[3] || 0 },
    { cls: 'bar-intensity-high', v: (pct[4] || 0) + (pct[5] || 0) }
  ];
  var bar = h('div', 'zone-split' + (compact ? ' is-compact' : ''));
  parts.forEach(function (p) {
    var seg = h('span', 'zone-split-seg ' + p.cls);
    seg.style.width = p.v.toFixed(2) + '%';
    bar.appendChild(seg);
  });
  bar.appendChild(h('span', 'zone-split-ref'));
  bar.setAttribute('role', 'img');
  bar.setAttribute('aria-label', '低強度 ' + parts[0].v.toFixed(1) + '%，中強度 '
    + parts[1].v.toFixed(1) + '%，高強度 ' + parts[2].v.toFixed(1) + '%');
  return bar;
}

export function renderOverviewZones(ov) {
  var node = $('overview-zones');
  clear(node);
  var zones = ov.hr_zones;
  var bounds = ov.hr_zone_bounds;
  var tipText = [
    '上方時間區間（目前 ' + rangeChip(ov) + '）內所有跑步在 Z1～Z5 的停留時間加總，來自手錶每場記錄的區間時間。'
      + '佔比的分母只含 Z1～Z5，不含低於 Z1 的暖身與停等時間。',
    '上方比例條：低強度 Z1–Z2、中強度 Z3、高強度 Z4–Z5；黑線標在 80% 的位置，對照 80/20 原則。'
  ];
  if (bounds.available) {
    tipText.push('bpm 範圍依手錶 %HRR 區間設定換算：靜止心率 ' + bounds.resting_hr_bpm
      + ' ＋ %HRR ×（最大心率 ' + bounds.max_hr.value + ' − ' + bounds.resting_hr_bpm
      + '）。最大心率為手錶顯示值，若比實際高，所有區間都會跟著偏高。');
  }
  var head = overviewHead('心率區間分佈',
    rangeChip(ov) + (zones.available ? ' · ' + zones.sessions_counted + ' 場' : ''), tipText);
  node.appendChild(head);

  if (!zones.available) {
    node.appendChild(h('div', 'state-msg state-unavailable', zones.reason));
    return;
  }
  node.appendChild(zoneSplitBar(zones.zones, false));
  var pct = {};
  zones.zones.forEach(function (z) { pct[z.zone] = z; });
  var low = (pct[1].pct || 0) + (pct[2].pct || 0);
  var high = (pct[4].pct || 0) + (pct[5].pct || 0);
  node.appendChild(h('div', 'zone-split-labels',
    '低 ' + C.formatNumber(low, 1) + '%　中 ' + C.formatNumber(pct[3].pct, 1) + '%　高 ' + C.formatNumber(high, 1) + '%'));

  var list = h('div', 'zone-rows');
  bounds.zones.forEach(function (b) {
    var z = pct[b.zone] || { seconds: 0, pct: 0 };
    var row = h('div', 'zone-row');
    var name = h('span', 'zone-name');
    name.appendChild(h('strong', null, 'Z' + b.zone + ' ' + b.name));
    name.appendChild(h('span', 'zone-name-en', b.name_en));
    row.appendChild(name);
    row.appendChild(h('span', 'zone-bpm', bounds.available ? b.bpm_low + '–' + b.bpm_high : ''));
    var track = h('span', 'zone-track');
    var fill = h('span', 'zone-fill bar-zone-fill-' + b.zone);
    fill.style.width = (z.pct || 0) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    var val = h('span', 'zone-pct', C.formatNumber(z.pct, 1) + '%');
    val.title = C.formatDuration(z.seconds);
    row.appendChild(val);
    list.appendChild(row);
  });
  node.appendChild(list);
  node.appendChild(h('p', 'chart-note', '共 ' + C.formatDuration(zones.total_seconds)
    + (bounds.available ? '　bpm 依手錶 %HRR 設定換算（最大 ' + bounds.max_hr.value + '、靜止 ' + bounds.resting_hr_bpm + '）' : '　尚未設定手錶區間，不顯示 bpm')));
}

export function renderOverview() {
  var ov = state.overview;
  if (!ov) return; // 還沒回來：載入中訊息由 loadOverview() 放
  // 重繪會換掉所有 ⓘ 按鈕；開著的說明小視窗若不關，會失去對應的按鈕
  closeInfoPopover(false);
  if (!ov.available) {
    OVERVIEW_NODE_IDS.forEach(function (id) { showUnavailable($(id), ov.reason); });
    return;
  }
  renderOverviewStrip(ov);
  renderOverviewRecent(ov);
  renderOverviewVolume(ov);
  renderOverviewZones(ov);
}

export function loadOverview() {
  OVERVIEW_NODE_IDS.forEach(function (id) { showLoading($(id), '載入總覽'); });
  api('/api/overview?range=' + state.range).then(function (data) {
    state.overview = data;
    renderOrDefer('overview', renderOverview);
  }).catch(function (err) {
    OVERVIEW_NODE_IDS.forEach(function (id) { showError($(id), err.message); });
  });
}
