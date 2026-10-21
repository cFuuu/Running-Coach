/**
 * wellness.js — 身體狀況 view：每日指標折線與指標自訂（排序／顯隱）
 */

import * as C from '../charts.js';
import { state, wellnessMetricDefs, orderedVisibleWellnessDefs, saveWellnessSettings } from '../core/state.js';
import { $, clear, h, widthOf, wrapChart } from '../core/dom.js';

/** 依 def.format 選對應的格式化函式（'duration' 用時分秒，其餘用一般數字）。 */
export function wellnessFormatter(def) {
  if (def.format === 'duration') return function (v) { return C.formatDuration(v); };
  return function (v) { return C.formatNumber(v, def.decimals); };
}

/**
 * @param {boolean} [keepPanelOpen] 重繪後是否自動重新展開自訂面板——
 *   排序/顯隱操作都會呼叫 renderWellness() 整批重繪（含面板容器本身
 *   一併清掉重建），若每次操作都要使用者手動重新點開面板，連續調整
 *   多個指標的順序會非常繁瑣，故排序/勾選操作一律傳 true。
 */
export function renderWellness(keepPanelOpen) {
  var node = $('wellness-charts');
  clear(node);
  if (!state.wellness) return;

  var customizeWrap = wellnessCustomizeButton();
  node.appendChild(customizeWrap);
  if (keepPanelOpen) customizeWrap.appendChild(buildWellnessCustomizePanel());

  var metrics = state.wellness.metrics || {};
  var startDate = state.wellness.start_date;
  var endDate = state.wellness.end_date;

  orderedVisibleWellnessDefs().forEach(function (def) {
    var m = metrics[def.key];
    var formatter = wellnessFormatter(def);
    var card = h('div', 'chart-block');
    var titleRow = h('div', 'chart-title-row');
    titleRow.appendChild(h('h3', 'chart-title', def.label + (def.unit ? '（' + def.unit + '）' : '')));

    if (!m) {
      card.appendChild(titleRow);
      card.appendChild(h('div', 'state-msg state-unavailable', '後端未提供此指標'));
      node.appendChild(card);
      return;
    }

    // clipped:true → 明確提示，且絕不把缺的部分畫成 0 或平線
    if (m.clipped) {
      var cov = m.coverage && m.coverage.earliest_date ? m.coverage.earliest_date : null;
      titleRow.appendChild(h('span', 'badge badge-clipped',
        cov ? (cov + ' 前無資料') : '此日期前無資料'));
    }
    card.appendChild(titleRow);

    var points = (m.points || []).filter(function (p) {
      return typeof p.value === 'number' && isFinite(p.value);
    });

    if (m.available === false || points.length === 0) {
      card.appendChild(h('div', 'state-msg state-unavailable',
        m.reason || '此區間內無資料'));
      node.appendChild(card);
      return;
    }

    // 圖表 X 軸起點：clipped 時從實際有資料的第一天畫起，避免產生誤導的空白平線
    var chartStart = startDate;
    if (m.clipped && m.coverage && m.coverage.earliest_date && m.coverage.earliest_date > startDate) {
      chartStart = m.coverage.earliest_date;
    }
    if (points[0].date > chartStart) chartStart = points[0].date;

    card.appendChild(wrapChart(C.wellnessChart(points, {
      label: def.label + '折線圖',
      startDate: chartStart,
      endDate: endDate,
      trainingDays: state.trainingDays,
      formatter: formatter,
      lineClass: 'line-metric-' + def.key,
      dotClass: 'dot-metric',
      containerWidth: widthOf(node)
    })));

    var latest = points[points.length - 1];
    card.appendChild(h('div', 'chart-note',
      '最新（' + latest.date + '）：' + formatter(latest.value)
      + (def.unit && def.format !== 'duration' ? ' ' + def.unit : '') + '　・　共 ' + points.length + ' 天有資料'));
    node.appendChild(card);
  });
}

/**
 * 「⚙ 自訂」按鈕：展開一個可排序／可勾選顯示的指標清單面板。
 * 排序用上下箭頭而非拖曳——HTML5 drag&drop 在行動瀏覽器完全無效
 * （觸控不會觸發 drag 事件），自製觸控拖曳成本遠超單人自用工具的需求；
 * 上下箭頭鍵盤可操作、觸控可點擊（≥44px），且資料模型（有序 key 陣列）
 * 與拖曳互動層完全獨立，日後真要換成拖曳也不需要動這裡的儲存格式。
 */
export function wellnessCustomizeButton() {
  var wrap = h('div', 'wellness-customize');
  var btn = h('button', 'wellness-customize-toggle', '⚙ 自訂');
  btn.type = 'button';
  btn.addEventListener('click', function () { toggleWellnessCustomizePanel(wrap); });
  wrap.appendChild(btn);
  return wrap;
}

export function toggleWellnessCustomizePanel(wrap) {
  var existing = wrap.querySelector('.wellness-customize-panel');
  if (existing) { existing.parentNode.removeChild(existing); return; }
  wrap.appendChild(buildWellnessCustomizePanel());
}

export function buildWellnessCustomizePanel() {
  var panel = h('div', 'wellness-customize-panel');
  var defs = wellnessMetricDefs();
  var visible = orderedVisibleWellnessDefs();
  var visibleKeys = visible.map(function (d) { return d.key; });
  // 面板要能勾選「目前隱藏」的指標重新顯示，所以完整順序 = 目前可見的
  // （依使用者順序）+ 目前隱藏的（依 defs 原始順序，排在後面）
  var hiddenDefs = defs.filter(function (d) { return visibleKeys.indexOf(d.key) === -1; });
  var fullOrder = visible.concat(hiddenDefs);

  function currentHiddenSet() {
    var hidden = {};
    hiddenDefs.forEach(function (d) { hidden[d.key] = true; });
    return hidden;
  }

  function persistAndRerender(order, hiddenSet) {
    var hiddenList = order.filter(function (k) { return hiddenSet[k]; });
    saveWellnessSettings(order, hiddenList);
    renderWellness(true); // 保持面板開啟，讓連續排序/勾選操作不必每次重新點開
  }

  var list = h('div', 'wellness-customize-list');
  var hiddenSet = currentHiddenSet();

  fullOrder.forEach(function (def, idx) {
    var row = h('div', 'wellness-customize-row');

    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !hiddenSet[def.key];
    checkbox.addEventListener('change', function () {
      var order = fullOrder.map(function (d) { return d.key; });
      if (checkbox.checked) delete hiddenSet[def.key]; else hiddenSet[def.key] = true;
      persistAndRerender(order, hiddenSet);
    });
    row.appendChild(checkbox);
    row.appendChild(h('span', 'wellness-customize-label', def.label));

    var upBtn = h('button', 'wellness-customize-move', '↑');
    upBtn.type = 'button';
    upBtn.disabled = idx === 0;
    upBtn.addEventListener('click', function () {
      var order = fullOrder.map(function (d) { return d.key; });
      var tmp = order[idx - 1]; order[idx - 1] = order[idx]; order[idx] = tmp;
      persistAndRerender(order, hiddenSet);
    });
    var downBtn = h('button', 'wellness-customize-move', '↓');
    downBtn.type = 'button';
    downBtn.disabled = idx === fullOrder.length - 1;
    downBtn.addEventListener('click', function () {
      var order = fullOrder.map(function (d) { return d.key; });
      var tmp = order[idx + 1]; order[idx + 1] = order[idx]; order[idx] = tmp;
      persistAndRerender(order, hiddenSet);
    });
    row.appendChild(upBtn);
    row.appendChild(downBtn);

    list.appendChild(row);
  });

  panel.appendChild(list);
  return panel;
}

//
// 回答「最近狀態怎樣」的跨週數據頁（2026-10-14 依 Fu 的參考圖改版）：
//   摘要列（區間摘要／資料品質／資料新鮮度）→ 5 張指標卡 → 週跑量＋心率區間並排 → 最佳成績
// 資料來自兩支端點：
//   /api/overview       除 ATL/CTL/TSB 以外的全部
//   /api/training-load  ATL/CTL/TSB（與訓練負荷頁共用 state.loadSeries，不另抓）
// 每張卡標出時間範圍（window-chip），ⓘ 只寫定義與算法，不寫判讀（判讀屬 AI 教練面板）。
// 增減幅度等標籤一律用中性樣式，不用紅綠暗示好壞。
