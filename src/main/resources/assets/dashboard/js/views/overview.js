/**
 * overview.js — 總覽 view：摘要列、指標卡、週跑量、心率區間、本週課表、個人最佳成績
 */

import * as C from '../charts.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h, showLoading, showError, showUnavailable, widthOf, wrapChart } from '../core/dom.js';
import { infoTip, closeInfoPopover, confBadge, kpiCard } from '../core/facts.js';

import { navigate, renderOrDefer } from '../shell/router.js';

// 回答「最近狀態怎樣」的跨週數據頁（2026-10-14 依 Fu 的參考圖改版）：
//   摘要列（區間摘要／資料品質／資料新鮮度）→ 5 張指標卡 → 週跑量＋心率區間並排 → 最佳成績
// 資料來自兩支端點：
//   /api/overview       除 ATL/CTL/TSB 以外的全部
//   /api/training-load  ATL/CTL/TSB（與訓練負荷頁共用 state.loadSeries，不另抓）
// 每張卡標出時間範圍（window-chip），ⓘ 只寫定義與算法，不寫判讀（判讀屬 AI 教練面板）。
// 增減幅度等標籤一律用中性樣式，不用紅綠暗示好壞。

// 強度分層的顯示資訊。分層本身（哪些 zone 算低／中／高）以後端回傳的
// weekly_intensity.tiers 為準，這裡只決定顏色與名稱。
export var INTENSITY_TIERS = [
  { key: 'low', name: '低強度', className: 'bar-intensity-low' },
  { key: 'mid', name: '中強度', className: 'bar-intensity-mid' },
  { key: 'high', name: '高強度', className: 'bar-intensity-high' },
  { key: 'unknown', name: '未分類', className: 'bar-intensity-unknown' }
];

// 最佳成績卡每個距離預設顯示幾場（第 1 名為最佳，其餘為次佳）

export var OVERVIEW_NODE_IDS = ['overview-strip', 'overview-kpis', 'overview-volume', 'overview-zones', 'overview-pbs'];

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

export function vdotCard(vdot) {
  var race = vdot.race || {};
  var ref = vdot.garmin_reference;
  var refText = null;
  if (ref && ref.predictions.length > 0) {
    var vals = ref.predictions.map(function (p) { return p.vdot; });
    var lo = Math.min.apply(null, vals);
    var hi = Math.max.apply(null, vals);
    refText = 'Garmin 對照 ' + C.formatNumber(lo, 1) + (hi !== lo ? '–' + C.formatNumber(hi, 1) : '');
  }
  var tip = [
    'Jack Daniels 的跑力指數：用一場全力成績反推的有氧能力分數。',
    '只採用標記為比賽或全力測驗的成績（3K／5K／10K 需在 90 天內，半馬／全馬 180 天內），'
      + '把實際距離與完賽時間代入 Daniels & Gilbert 公式：VDOT ＝ 該配速的攝氧成本 ÷ 能持續該時間的最大攝氧比例。'
      + '有手動計時成績的場次用手動計時成績。',
    '走勢線是歷年每場比賽各自算出的 VDOT（不受新鮮度門檻限制）。',
    '不採用一般訓練經心率換算的推估：那是次最大強度推估，對訓練量少的跑者會偏高。'
  ];
  if (ref) {
    tip.push('Garmin 對照值：把手錶 ' + ref.as_of + ' 的完賽預測（'
      + ref.predictions.map(function (p) {
        return p.distance + ' ' + C.formatDuration(p.time_sec) + ' → ' + C.formatNumber(p.vdot, 1);
      }).join('、')
      + '）用同一公式換算。Garmin 演算法未公開，僅供對照，不作為依據。');
  }
  var history = (vdot.history || []).map(function (x) { return x.vdot; });
  if (!race.value) {
    return kpiCard({
      label: 'VDOT', chip: '最近全力成績', value: '未評',
      badge: h('span', 'conf-badge conf-none', '無資料'),
      note: race.reason + (refText ? '；' + refText : ''),
      series: history.length > 1 ? history : null,
      tip: tip
    });
  }
  return kpiCard({
    label: 'VDOT',
    chip: '最近全力成績',
    value: C.formatNumber(race.value.value, 1),
    badge: confBadge(race.value),
    note: C.shortDate(race.basis.date) + ' ' + race.basis.distance + (refText ? '　' + refText : ''),
    series: history.length > 1 ? history : null,
    tip: tip
  });
}

export function thisWeekCard(w) {
  var card = kpiCard({
    label: '本週跑量',
    chip: C.shortDate(w.week_start) + '–' + C.shortDate(w.week_end),
    value: C.formatNumber(w.done_km, 1),
    unit: w.planned_km !== null ? '/ ' + C.formatNumber(w.planned_km, 1) + ' km' : 'km',
    note: w.planned_km === null
      ? '本週無課表'
      : (w.planned_without_distance > 0 ? '課表另 ' + w.planned_without_distance + ' 堂只給時間' : '課表距離'),
    tip: [
      '本週（週一至週日，以今天為準，不受上方時間區間影響）已完成的跑步距離，斜線後為課表距離。',
      '課表距離只加總有寫距離的課；只寫時間的課（例如 FR 60 分鐘）無法換算距離，另外註明堂數，不拿配速去猜。'
    ]
  });
  if (w.planned_km) {
    var pct = Math.min(100, (w.done_km / w.planned_km) * 100);
    var bar = h('div', 'kpi-bar');
    var fill = h('div', 'kpi-bar-fill');
    fill.style.width = pct.toFixed(1) + '%';
    bar.appendChild(fill);
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', '本週跑量佔課表距離 ' + Math.round(pct) + '%');
    card.insertBefore(bar, card.querySelector('.kpi-note'));
  }
  return card;
}

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

export function zone2Card(w, bounds) {
  var secs = {};
  w.zone_seconds.forEach(function (z) { secs[z.zone] = z.seconds; });
  var total = w.zone_seconds.reduce(function (s, z) { return s + z.seconds; }, 0);
  var z2 = bounds.zones[1];
  var range = bounds.available ? '（' + z2.bpm_low + '–' + z2.bpm_high + ' bpm）' : '';
  var card = kpiCard({
    label: '本週 Zone 2 時間',
    chip: C.shortDate(w.week_start) + '–' + C.shortDate(w.week_end),
    value: total > 0 ? C.formatDuration(secs[2] || 0) : '—',
    badge: total > 0 ? h('span', 'neutral-badge', '佔本週 ' + C.formatNumber((secs[2] || 0) / total * 100, 0) + '%') : null,
    note: total > 0
      ? '本週心率區間共 ' + C.formatDuration(total) + '，' + w.zone_sessions_counted + '/' + w.sessions + ' 場有資料'
      : (w.sessions > 0 ? '本週跑步皆無心率區間資料' : '本週尚未跑步'),
    tip: [
      '本週（週一至週日）跑步在 Z2 輕鬆（Easy）' + range + '的停留時間加總，來自手錶每場記錄的區間時間。',
      '百分比的分母是本週跑步在 Z1～Z5 的總時間。'
    ]
  });
  if (total > 0) card.insertBefore(zoneSplitBar(w.zone_seconds.map(function (z) {
    return { zone: z.zone, pct: z.seconds / total * 100 };
  }), true), card.querySelector('.kpi-note'));
  return card;
}

/** 訓練負荷卡：主數字 TSB，附「佔 CTL 的百分比」讓自訂尺度的數字可讀。 */
export function loadCard() {
  var rows = (state.loadSeries && state.loadSeries.series) || [];
  var last = null;
  for (var i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i].tsb && typeof rows[i].tsb.value === 'number') { last = rows[i]; break; }
  }
  var tip = [
    'TSB（訓練壓力平衡）＝ CTL − ATL。ATL 是每日訓練負荷的 7 日指數加權平均（近期疲勞），CTL 是 42 日指數加權平均（長期體能基礎）。',
    '負荷採 Banister TRIMP：分鐘 × 心率儲備百分比（%HRR）× 隨強度指數上升的權重，'
      + '一小時輕鬆跑約 85、一小時閾值跑約 165。另外標出 TSB 佔 CTL 的百分比：表示近期負荷比長期基礎高（負）或低（正）多少。',
    '參考值（Joe Friel，TrainingPeaks〈Applying the Numbers Part 3〉，原為 TSS 尺度的 TSB）：'
      + '比賽日 +15～+25 通常表現最好（部分選手 +5～+10）；−10～+10 是過渡區，停留時間應短；'
      + '負值代表處於疲勞、多半不在比賽狀態。',
    'TRIMP 與 TSS 定義不同（同一小時閾值跑約為 TSS 的 1.6 倍），上面的絕對值不能直接套在 TSB 上。'
      + 'intervals.icu 的做法是把同一組數字改套在「TSB 佔 CTL 的百分比」，使用者社群常引用 −30% 以下為高風險區'
      + '——這是社群慣例，不是官方文件或研究定論。以上僅供對照，本頁不據此判讀。'
  ];
  if (!last) {
    return kpiCard({
      label: '訓練負荷', value: '—', tip: tip,
      note: state.loadSeries ? '此區間內無訓練負荷資料' : '載入中…'
    });
  }
  var tsb = last.tsb.value;
  var ctl = last.ctl && last.ctl.value;
  return kpiCard({
    label: '訓練負荷',
    chip: '截至 ' + C.shortDate(last.date),
    value: (tsb > 0 ? '+' : '') + C.formatNumber(tsb, 0),
    unit: 'TSB',
    badge: ctl ? h('span', 'neutral-badge', 'CTL 的 ' + (tsb > 0 ? '+' : '−') + Math.abs(tsb / ctl * 100).toFixed(0) + '%') : null,
    note: 'ATL ' + C.formatNumber(last.atl && last.atl.value, 0) + ' · CTL ' + C.formatNumber(ctl, 0),
    series: rows.map(function (r) { return r.tsb ? r.tsb.value : null; }),
    tip: tip
  });
}

export function renderOverviewKpis(ov) {
  var node = $('overview-kpis');
  clear(node);
  var strip = h('div', 'kpi-strip overview-kpi-strip');
  strip.appendChild(vdotCard(ov.vdot));
  strip.appendChild(thisWeekCard(ov.this_week));
  strip.appendChild(recentVolumeCard(ov.recent_volume, ov.weekly_intensity.weeks));
  strip.appendChild(zone2Card(ov.this_week, ov.hr_zone_bounds));
  var load = loadCard();
  // 信賴度 badge 與「佔 CTL」標籤同列會擠，信賴度改放在 ATL/CTL 那行
  var lastTsb = null;
  ((state.loadSeries && state.loadSeries.series) || []).forEach(function (r) { if (r.tsb) lastTsb = r.tsb; });
  var note = load.querySelector('.kpi-note');
  if (lastTsb && note) note.appendChild(confBadge(lastTsb));
  strip.appendChild(load);
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

// ---- 最佳成績

// 展開中的距離（distance 代碼）；重繪總覽時保留，切換頁面再回來也還在
export var pbOpenDistance = null;

/** 卡片上的賽事名稱：個人測驗一律顯示「個人測驗」，完整名稱留給展開清單 */
export function pbCardName(r) {
  return r.personal_test ? '個人測驗' : (r.name || '');
}

export function renderOverviewPbs(ov) {
  var node = $('overview-pbs');
  clear(node);
  var head = overviewHead('個人最佳成績', '生涯', [
    '標記為 Race 的場次（含個人計時測驗），每個距離只顯示最佳一筆；點卡片可展開該距離的全部成績，由快到慢排列，點清單中任一筆可看單場分析。不受上方時間區間影響。',
    '成績預設用手錶紀錄的時間；有特別填入時間的場次（大會成績或現場計時）改用填入值，展開清單中標「手動」。配速一律為「時間 ÷ 標準距離」，不用手錶 GPS 量到的距離。',
    '賽事名稱讀自學員檔 athlete_profile.md 的「比賽成績」表（以日期對應），沒寫的場次用 Garmin 活動名稱；名稱含「測驗」或「測試」的在卡片上顯示為「個人測驗」。每場的 VDOT 用同一條 Daniels 公式算出。'
  ]);
  node.appendChild(head);

  var byDistance = {};
  ov.personal_bests.forEach(function (pb) { byDistance[pb.distance] = pb; });
  if (pbOpenDistance && !(byDistance[pbOpenDistance] && byDistance[pbOpenDistance].records.length)) {
    pbOpenDistance = null;
  }

  var grid = h('div', 'pb-grid');
  ov.personal_bests.forEach(function (pb) {
    grid.appendChild(pbCard(pb));
  });
  node.appendChild(grid);
  if (pbOpenDistance) node.appendChild(pbDetail(byDistance[pbOpenDistance]));
}

export function pbCard(pb) {
  var count = pb.records.length;
  var expandable = count > 0;
  var card = h(expandable ? 'button' : 'div', 'pb-card');
  if (count === 0) card.className += ' is-empty';
  if (expandable) {
    card.type = 'button';
    card.className += ' is-expandable' + (pbOpenDistance === pb.distance ? ' is-open' : '');
    card.setAttribute('aria-expanded', pbOpenDistance === pb.distance ? 'true' : 'false');
    card.setAttribute('aria-controls', 'pb-detail');
    card.addEventListener('click', function () {
      pbOpenDistance = pbOpenDistance === pb.distance ? null : pb.distance;
      renderOverviewPbs(state.overview);
      var target = pbOpenDistance ? $('pb-detail') : null;
      if (target) target.focus({ preventScroll: true });
    });
  }

  var top = h('div', 'pb-card-head');
  top.appendChild(h('span', 'pb-distance', pb.label));
  if (expandable) top.appendChild(h('span', 'pb-count', count > 1 ? '共 ' + count + ' 場' : ''));
  card.appendChild(top);

  if (count === 0) {
    card.appendChild(h('div', 'pb-empty-text', '尚無紀錄'));
    return card;
  }
  var best = pb.records[0];
  card.appendChild(h('div', 'pb-time', C.formatDuration(best.time_sec)));
  card.appendChild(h('div', 'pb-pace', C.formatPace(best.pace_sec_per_km) + '/km'));
  card.appendChild(h('div', 'pb-date', best.date));
  card.appendChild(h('div', 'pb-vdot', 'VDOT ' + C.formatNumber(best.vdot, 1)));
  var name = h('div', 'pb-name', pbCardName(best));
  name.title = best.name || '';
  card.appendChild(name);
  return card;
}

export function pbDetail(pb) {
  var panel = h('div', 'pb-detail');
  panel.id = 'pb-detail';
  panel.tabIndex = -1;
  var head = h('div', 'pb-detail-head');
  head.appendChild(h('h3', 'pb-detail-title', pb.label + ' 歷史成績（共 ' + pb.records.length + ' 場'
    + (pb.records.length > 1 ? '，由快到慢）' : '）')));
  var close = h('button', 'pb-detail-close', '✕');
  close.type = 'button';
  close.setAttribute('aria-label', '收合 ' + pb.label + ' 歷史成績');
  close.addEventListener('click', function () {
    pbOpenDistance = null;
    renderOverviewPbs(state.overview);
  });
  head.appendChild(close);
  panel.appendChild(head);

  var list = h('ol', 'pb-list');
  pb.records.forEach(function (r, i) {
    var item = h('li');
    var row = h('button', 'pb-row' + (i === 0 ? ' is-best' : ''));
    row.type = 'button';
    row.setAttribute('aria-label', '第 ' + (i + 1) + ' 名 ' + C.formatDuration(r.time_sec) + '，' + r.date + '，' + (r.name || '') + '，檢視單場分析');
    row.appendChild(h('span', 'pb-row-rank', String(i + 1)));
    var time = h('span', 'pb-row-time', C.formatDuration(r.time_sec));
    if (r.official) {
      var manual = h('span', 'pb-manual', '手動');
      manual.title = '時間為人工填入（大會成績或現場計時），非手錶紀錄';
      time.appendChild(manual);
    }
    row.appendChild(time);
    row.appendChild(h('span', 'pb-row-pace', C.formatPace(r.pace_sec_per_km) + '/km'));
    row.appendChild(h('span', 'pb-row-date', r.date));
    row.appendChild(h('span', 'pb-row-name', r.name || ''));
    row.appendChild(h('span', 'pb-row-vdot', 'VDOT ' + C.formatNumber(r.vdot, 1)));
    row.addEventListener('click', function () { navigate('sessions', r.id); });
    item.appendChild(row);
    list.appendChild(item);
  });
  panel.appendChild(list);
  return panel;
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
  renderOverviewKpis(ov);
  renderOverviewVolume(ov);
  renderOverviewZones(ov);
  renderOverviewPbs(ov);
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
