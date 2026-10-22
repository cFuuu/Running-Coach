/**
 * trends.js — 趨勢 view：依類別排列的走勢圖（吃頂列時間範圍）
 *
 * 類別與預設順序（2026-10-21 與 Fu 議定）：訓練負荷 → 恢復與身體 → 強度分布 → 能力 → 訓練量。
 * 2026-10-22 加入「Garmin 數據」（恢復與身體之後），卡片定義在 views/garmin.js。
 * 分類依據見 docs/dev/FRONTEND_REFACTOR.md 階段 6；首頁放「現在的值」，這裡放「走勢」。
 *
 * 自訂（core/customize.js）：類別的順序與顯隱（偏好 key 'trends'），以及各類別內圖表的
 * 順序與顯隱（'trends-<類別>'；恢復與身體沿用既有的 'wellness'，使用者原本的設定不遺失）。
 *
 * 每張圖是一個 render(node) 函式，資料各自來自 state（loadSeries／wellness／sessions／
 * overview），任一份資料回來就整頁重畫——頁面上的圖寬度依容器而定，局部更新反而要
 * 追蹤哪張圖在哪，整頁重畫最單純。整頁是一個同步游標群組：所有圖的 X 軸都是日期。
 */

import { state, wellnessMetricDefs } from '../core/state.js';
import { $, clear, h, showLoading } from '../core/dom.js';
import { closeInfoPopover } from '../core/facts.js';
import { customizer, visibleDefs } from '../core/customize.js';
import { renderTsbChart, renderGarminAcuteChart, renderSessionTrend, SESSION_TRENDS } from './load.js';
import { renderWellnessMetric } from './wellness.js';
import { renderOverviewStrip, renderWeeklyVolume, renderRecentVolume, renderZones } from './overview.js';
import { GARMIN_CARD_DEFS, renderGarminCard } from './garmin.js';

// 歸在「訓練負荷」而非「恢復與身體」的 Garmin 每日指標
var LOAD_WELLNESS_KEYS = ['acwr', 'recovery_time_hours'];

function wellnessDefsFor(keys, include) {
  return wellnessMetricDefs()
    .filter(function (d) { return (keys.indexOf(d.key) !== -1) === include; })
    .map(function (d) {
      return {
        key: d.key,
        label: d.label,
        default_hidden: d.default_hidden,
        render: function (node) { renderWellnessMetric(d, node); }
      };
    });
}

/** Garmin 卡片：資料還沒回來時顯示載入中（整頁重畫時各卡各自判斷）。 */
function garminCardDefs() {
  return GARMIN_CARD_DEFS.map(function (d) {
    return {
      key: d.key,
      label: d.label,
      render: function (node) {
        if (!state.garminCards) { showLoading(node, '載入 ' + d.label); return; }
        renderGarminCard(d.key, state.garminCards.cards[d.key], node);
      }
    };
  });
}

function sessionTrend(key) {
  var spec = SESSION_TRENDS[key];
  return { key: key, label: spec.title, render: function (node) { renderSessionTrend(spec, node); } };
}

/** 類別定義：charts() 每次重畫時呼叫（身體指標的清單來自 /api/meta，啟動後才確定）。 */
var CATEGORIES = [
  {
    key: 'load', label: '訓練負荷', settingsKey: 'trends-load',
    charts: function () {
      return [
        { key: 'tsb', label: 'TSB（訓練壓力平衡）', render: renderTsbChart },
        { key: 'garmin_acute', label: 'Garmin 急性負荷', render: renderGarminAcuteChart }
      ].concat(wellnessDefsFor(LOAD_WELLNESS_KEYS, true));
    }
  },
  {
    key: 'body', label: '恢復與身體', settingsKey: 'wellness', hint: '淡色背景為訓練日',
    charts: function () { return wellnessDefsFor(LOAD_WELLNESS_KEYS, false); }
  },
  {
    // 2026-10-22：仿 Garmin Connect 網頁的卡片牆。卡片小、一列多張，故用自己的格線
    key: 'garmin', label: 'Garmin 數據', settingsKey: 'trends-garmin',
    hint: '手錶與 Garmin Connect 的輸出，僅供顯示，不納入本專案判斷',
    gridClass: 'gc-grid', cardClass: 'gc-slot',
    charts: garminCardDefs
  },
  {
    key: 'intensity', label: '強度分布', settingsKey: 'trends-intensity',
    charts: function () {
      return [{ key: 'zones', label: '心率區間分佈', render: renderZones }, sessionTrend('avg_hr')];
    }
  },
  {
    key: 'ability', label: '能力', settingsKey: 'trends-ability',
    charts: function () { return [sessionTrend('pace'), sessionTrend('te')]; }
  },
  {
    key: 'volume', label: '訓練量', settingsKey: 'trends-volume',
    charts: function () {
      return [
        { key: 'weekly', label: '每週跑量（依強度堆疊）', render: renderWeeklyVolume },
        { key: 'recent', label: '近幾週跑量', render: renderRecentVolume },
        sessionTrend('distance')
      ];
    }
  }
];

export function renderTrendsPage() {
  var body = $('trends-body');
  if (!body) return;
  closeInfoPopover(false); // 重畫會換掉 ⓘ 按鈕，開著的說明要先關
  renderOverviewStrip();

  var head = $('trends-customize');
  clear(head);
  head.appendChild(customizer({
    settingsKey: 'trends',
    defs: CATEGORIES,
    label: '⚙ 自訂類別',
    hint: '調整類別順序，取消勾選可隱藏整個類別。各類別內的圖表在類別標題旁自訂。',
    onChange: renderTrendsPage
  }));

  clear(body);
  // 先把所有容器放進頁面，再逐張畫：圖表寬度依容器實際寬度決定，必須已在 DOM 中
  var jobs = [];
  visibleDefs('trends', CATEGORIES).forEach(function (cat) {
    var section = h('section', 'panel trend-category');
    section.id = 'trend-cat-' + cat.key;
    var catHead = h('div', 'trend-cat-head');
    var title = h('h2', 'panel-title', cat.label);
    if (cat.hint) title.appendChild(h('span', 'panel-hint', cat.hint));
    catHead.appendChild(title);
    var charts = cat.charts();
    catHead.appendChild(customizer({
      settingsKey: cat.settingsKey,
      defs: charts,
      onChange: renderTrendsPage
    }));
    section.appendChild(catHead);

    var grid = h('div', cat.gridClass || 'trend-grid');
    var shown = visibleDefs(cat.settingsKey, charts);
    if (shown.length === 0) {
      grid.appendChild(h('div', 'state-msg state-unavailable', '這個類別的圖表都已隱藏，可在右上角「⚙ 自訂」重新勾選。'));
    }
    shown.forEach(function (chart) {
      var card = h('div', cat.cardClass || 'trend-card');
      grid.appendChild(card);
      jobs.push({ chart: chart, node: card });
    });
    section.appendChild(grid);
    body.appendChild(section);
  });

  jobs.forEach(function (job) { job.chart.render(job.node); });
}

/** 換時間範圍時：清掉舊範圍的資料並顯示載入中，避免新舊資料混在同一頁。 */
export function showTrendsLoading() {
  state.loadSeries = null;
  state.wellness = null;
  state.garminCards = null;
  state.overview = null;
  state.sessionsReady = false;
  var body = $('trends-body');
  if (body && !body.childNodes.length) showLoading(body, '載入趨勢');
}
