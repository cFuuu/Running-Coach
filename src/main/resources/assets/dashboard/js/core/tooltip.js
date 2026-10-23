/**
 * tooltip.js — 圖表 tooltip 與跨圖同步參考線（事件委派，全域只綁一次）
 */

import { $ } from './dom.js';

// 6 張圖共用同一套命中/顯示邏輯，統一在此處理，不在 charts.js 逐圖各寫一份
// （charts.js 的架構契約禁止該檔碰 addEventListener／document，見該檔檔頭）。
//
// 互動模式分兩種：
//   離散模式（心率區間圖）：沒有連續時間軸（X 軸是 Z1~Z5 分類），維持單圖
//   浮動 tooltip，不參與同步游標。
//
//   連續模式（其餘 5 張）：仿 Garmin 的「跨圖同步垂直參考線」——滑鼠在
//   同一個同步群組內任一圖上移動時，同一群組的每張圖都會在對應的邏輯 X
//   位置畫一條垂直線＋圓點，並在圖表右上角顯示該點數值。三個群組各自獨立
//   （X 軸基準不同，見 SYNC_GROUPS 常數），不跨群組同步。
//
// 綁事件的方式是「事件委派到 document.body，只綁一次」而非替每個 SVG 各綁
// 一次：app.js 在 resize 後會整批 clear() + 重新產生 SVG（見 bindResize），
// 若事件綁在個別 SVG 上，重繪後監聽器會全部消失。委派到穩定的 body 則不受
// 重繪影響，重繪再多次也不用重綁。
//
// 座標轉換：charts.js 的 SVG 內部座標系是固定 viewBox（720 寬，見該檔
// VB_WIDTH），與螢幕實際像素無關，不能直接用 event.offsetX。改用
// getScreenCTM().inverse() 把螢幕座標換算回 viewBox 座標，這是全站唯一
// 一處做這個轉換的地方。

// 同步群組：容器 id → 該群組內所有連續模式圖表共用同一條邏輯 X 軸。
// 兩組互不同步，因為 X 軸基準不同：單場分析＝經過秒數；趨勢頁的所有圖
// （訓練負荷、身體指標、每場趨勢、每週跑量）＝日期字串，字典序恰好與時間序
// 一致，可直接比較——所以整個趨勢頁是同一組，滑到某一天，所有類別的圖都標出同一天。
export var SYNC_GROUPS = ['session-body', 'view-trends'];

export var tooltipEl = null;

export function ensureTooltipEl() {
  if (tooltipEl) return tooltipEl;
  tooltipEl = document.createElement('div');
  tooltipEl.className = 'chart-tooltip';
  tooltipEl.setAttribute('role', 'status');
  tooltipEl.setAttribute('aria-hidden', 'true');
  document.body.appendChild(tooltipEl);
  return tooltipEl;
}

export function hideTooltip() {
  if (tooltipEl) tooltipEl.classList.remove('is-visible');
}

/** meta.tips 的元素可能是純字串，也可能是 {label, value} 結構
 * （見 charts.js 的 trendLineChart／wellnessChart）；純文字 tooltip
 * 場景不需要粗體分段，統一拼成一行字串即可。 */
export function tipToText(tip) {
  if (tip && typeof tip === 'object' && tip.rows) {
    return [tip.label].concat(tip.rows.map(function (r) { return r.name + ' ' + r.value; })).join('\n');
  }
  if (tip && typeof tip === 'object') return tip.label + ' ' + tip.value;
  return tip;
}

/** 把螢幕座標（clientX/clientY）換算成該 SVG 內部 viewBox 座標。 */
export function toSvgPoint(svg, clientX, clientY) {
  var ctm = svg.getScreenCTM();
  if (!ctm) return null;
  var pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  return pt.matrixTransform(ctm.inverse());
}

/** 在已排序的陣列中二分搜尋離 target 最近的索引（數字或字典序可比較的字串皆可）。 */
export function nearestIndex(xs, target) {
  var lo = 0;
  var hi = xs.length - 1;
  while (lo < hi) {
    var mid = (lo + hi) >> 1;
    if (xs[mid] < target) lo = mid + 1; else hi = mid;
  }
  if (lo > 0) {
    var distPrev = xs[lo - 1] < target ? target - xs[lo - 1] : xs[lo - 1] - target;
    var distCur = xs[lo] < target ? target - xs[lo] : xs[lo] - target;
    if (distPrev <= distCur) return lo - 1;
  }
  return lo;
}

/** 依 clientX/clientY 定位 tooltip，靠近視窗邊緣時翻轉方向避免溢出。 */
export function positionTooltip(clientX, clientY) {
  var el = ensureTooltipEl();
  var margin = 14;
  var rect = el.getBoundingClientRect();
  var left = clientX + margin;
  var top = clientY + margin;
  if (left + rect.width > window.innerWidth) left = clientX - rect.width - margin;
  if (top + rect.height > window.innerHeight) top = clientY - rect.height - margin;
  el.style.left = Math.max(4, left) + 'px';
  el.style.top = Math.max(4, top) + 'px';
}

export function showTooltipText(text, clientX, clientY) {
  var el = ensureTooltipEl();
  el.textContent = text;
  el.classList.add('is-visible');
  positionTooltip(clientX, clientY);
}

/**
 * 找出目前指標所在的 SVG 屬於哪個同步群組容器（回傳容器 DOM 節點），
 * 找不到就回傳 null（例如在群組外的圖，或還沒接上任何群組容器）。
 */
export function findSyncGroupContainer(svg) {
  for (var i = 0; i < SYNC_GROUPS.length; i += 1) {
    var container = $(SYNC_GROUPS[i]);
    if (container && container.contains(svg)) return container;
  }
  return null;
}

// 每個同步群組各自一層覆蓋用的游標圖層（垂直線＋圓點＋數值標籤），
// 掛在該圖各自的 svg 內，clear 時一併清掉不會殘留。
export function clearSyncCursor(svg) {
  var layer = svg.querySelector('.sync-cursor-layer');
  if (layer) layer.parentNode.removeChild(layer);
}

export var SVG_NS = 'http://www.w3.org/2000/svg';
export function svgEl(name, attrs) {
  var node = document.createElementNS(SVG_NS, name);
  Object.keys(attrs || {}).forEach(function (k) {
    if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, String(attrs[k]));
  });
  return node;
}

// 數值標籤的粗估字元寬度／高度（viewBox 單位）。SVG 文字無法在畫之前用
// DOM API 量實際寬度，用等寬粗估即可，標籤本身不要求像素級精準，只要
// 不明顯溢出。字級對應 styles.css 的 .sync-cursor-label text（13px），
// 比原本的 11px 更大更好讀（使用者反映原本字級太小）。
export var SYNC_LABEL_CHAR_W = 7.6;
export var SYNC_LABEL_PAD_X = 6;
export var SYNC_LABEL_H = 18;
export var SYNC_LABEL_GAP = 3; // label 與 value 之間的間距（viewBox 單位）

/**
 * 在單張圖的 SVG 上畫出同步游標：跨越整個繪圖高度的垂直線、（若該圖在
 * 這個邏輯 X 有值）一個圓點、以及貼在圓點旁的數值標籤（背景框＋文字，
 * 仿 Garmin 風格——每張圖各自顯示自己的數值，不集中成單一 tooltip）。
 *
 * tip 支援兩種形狀：
 *   字串（分圈圖、心率區間、逐秒曲線等）→ 整段用一般字重顯示
 *   {label, value}（跨場趨勢／身體狀況的日期類圖表）→ label（日期）用
 *     一般字重、value（數值）用粗體，兩個 <tspan> 拼在同一個 <text> 裡，
 *     讓使用者一眼就能抓到數字本身
 */
export function drawSyncCursorOnSvg(svg, meta, logicalX) {
  clearSyncCursor(svg);
  if (!meta || !meta.logicalXs || meta.logicalXs.length === 0) return;
  var idx = nearestIndex(meta.logicalXs, logicalX);
  var x = meta.xs[idx];
  var y = meta.yOf ? meta.yOf(idx) : null;
  var box = meta.box;
  var tip = meta.tips[idx];

  var layer = svgEl('g', { class: 'sync-cursor-layer' });
  layer.appendChild(svgEl('line', {
    x1: x, y1: box.top, x2: x, y2: box.top + box.height, class: 'sync-cursor-line'
  }));
  if (meta.dots) {
    // 多系列圖（charts.multiLineChart）：每條線各畫一個圓點，描邊用該系列色
    meta.dots(idx).forEach(function (d) {
      var dot = svgEl('circle', { cx: x, cy: d.y, r: 3.5, class: 'sync-cursor-dot' });
      if (d.color) dot.setAttribute('style', 'stroke:' + d.color);
      layer.appendChild(dot);
    });
  } else if (y !== null && y !== undefined) {
    layer.appendChild(svgEl('circle', { cx: x, cy: y, r: 3.5, class: 'sync-cursor-dot' }));
  }

  if (tip && typeof tip === 'object' && tip.rows) {
    layer.appendChild(buildRowsLabel(tip, x, box));
  } else if (tip) {
    var isStructured = typeof tip === 'object';
    var displayText = isStructured ? (tip.label + ' ' + tip.value) : tip;
    var labelY = y !== null && y !== undefined ? y : box.top + 12;
    var labelW = displayText.length * SYNC_LABEL_CHAR_W + SYNC_LABEL_PAD_X * 2;
    // 貼在垂直線右側；靠近圖表右緣時翻到左側，避免超出 viewBox
    var atRightEdge = x + 6 + labelW > box.left + box.width;
    var labelX = atRightEdge ? x - 6 - labelW : x + 6;
    var labelTop = Math.min(Math.max(labelY - SYNC_LABEL_H / 2, box.top), box.top + box.height - SYNC_LABEL_H);

    var group = svgEl('g', { class: 'sync-cursor-label' });
    group.appendChild(svgEl('rect', {
      x: labelX, y: labelTop, width: labelW, height: SYNC_LABEL_H, rx: 3
    }));
    var textNode = svgEl('text', {
      x: labelX + SYNC_LABEL_PAD_X, y: labelTop + SYNC_LABEL_H / 2 + 4, 'text-anchor': 'start'
    });
    if (isStructured) {
      var labelSpan = svgEl('tspan', {});
      labelSpan.textContent = tip.label + ' ';
      var valueSpan = svgEl('tspan', { class: 'sync-cursor-label-value' });
      valueSpan.textContent = tip.value;
      textNode.appendChild(labelSpan);
      textNode.appendChild(valueSpan);
    } else {
      textNode.textContent = tip;
    }
    group.appendChild(textNode);
    layer.appendChild(group);
    void SYNC_LABEL_GAP; // 間距目前靠字串中的空白達成，保留常數供未來調整
  }

  svg.appendChild(layer); // 必須最後 append 才會蓋在折線/長條之上
}

/** 粗估一段文字的寬度（viewBox 單位）：中日韓全形字約一個字級寬，其餘用 SYNC_LABEL_CHAR_W。 */
function estimateTextWidth(str) {
  var w = 0;
  for (var i = 0; i < str.length; i += 1) {
    w += str.charCodeAt(i) >= 0x2e80 ? 13 : SYNC_LABEL_CHAR_W;
  }
  return w;
}

var ROW_H = 17;
var SWATCH = 8;

/**
 * 多列游標標籤（tip = {label, rows:[{name, value, color}]}）：第一列日期，
 * 之後每個系列一列「色塊 名稱 數值」，數值粗體。貼在垂直線右側，放不下翻到左側；
 * 垂直方向靠繪圖區頂端，避免蓋住大部分折線的同時又不超出 viewBox。
 */
function buildRowsLabel(tip, x, box) {
  var lines = [tip.label].concat(tip.rows.map(function (r) { return r.name + ' ' + r.value; }));
  var textW = lines.reduce(function (m, s, i) {
    return Math.max(m, estimateTextWidth(s) + (i > 0 ? SWATCH + 5 : 0));
  }, 0);
  var labelW = textW + SYNC_LABEL_PAD_X * 2;
  var labelH = lines.length * ROW_H + 6;
  var atRightEdge = x + 8 + labelW > box.left + box.width;
  var labelX = atRightEdge ? Math.max(x - 8 - labelW, 0) : x + 8;
  var labelTop = box.top + 2;

  var group = svgEl('g', { class: 'sync-cursor-label' });
  group.appendChild(svgEl('rect', { x: labelX, y: labelTop, width: labelW, height: labelH, rx: 3 }));
  var head = svgEl('text', { x: labelX + SYNC_LABEL_PAD_X, y: labelTop + ROW_H - 2, 'text-anchor': 'start' });
  head.textContent = tip.label;
  group.appendChild(head);
  tip.rows.forEach(function (r, i) {
    var rowY = labelTop + ROW_H * (i + 2) - 2;
    var swatch = svgEl('rect', {
      x: labelX + SYNC_LABEL_PAD_X, y: rowY - SWATCH - 1, width: SWATCH, height: SWATCH, rx: 2,
      class: 'sync-cursor-swatch'
    });
    if (r.color) swatch.setAttribute('style', 'fill:' + r.color);
    group.appendChild(swatch);
    var t = svgEl('text', { x: labelX + SYNC_LABEL_PAD_X + SWATCH + 5, y: rowY, 'text-anchor': 'start' });
    var nameSpan = svgEl('tspan', {});
    nameSpan.textContent = r.name + ' ';
    var valueSpan = svgEl('tspan', { class: 'sync-cursor-label-value' });
    valueSpan.textContent = r.value;
    t.appendChild(nameSpan);
    t.appendChild(valueSpan);
    group.appendChild(t);
  });
  return group;
}

/**
 * 群組同步的核心：在 startSvg 上算出目前指標對應的邏輯 X，
 * 然後對同群組「每一張」有 __syncMeta 的圖各自畫游標與數值標籤
 * （標籤直接畫在各圖內部，不使用全域浮動 tooltip）。
 *
 * 讀 svg.__syncMeta 而非 svg.querySelector('.hit').__chartHitMeta：
 * 離散模式圖表（分圈圖、週跑量長條圖）不畫額外的覆蓋矩形以免蓋掉原本
 * 逐項的 hover 命中，但仍會用 attachSyncMeta() 把 meta 直接掛在 svg 上，
 * 讓它們能「被動接收」同群組其他圖觸發的同步游標，見 charts.js 的
 * attachSyncMeta 說明。
 */
export function updateSyncGroup(container, startSvg, startMeta, clientX, clientY) {
  var pt = toSvgPoint(startSvg, clientX, clientY);
  if (!pt || startMeta.xs.length === 0) return;
  var startIdx = nearestIndex(startMeta.xs, pt.x);
  var logicalX = startMeta.logicalXs[startIdx];

  var svgs = container.querySelectorAll('.chart-svg');
  svgs.forEach(function (svg) {
    var meta = svg.__syncMeta;
    if (!meta) { clearSyncCursor(svg); return; } // 該圖無連續軸資料（如缺值狀態）
    drawSyncCursorOnSvg(svg, meta, logicalX);
  });
  hideTooltip(); // 群組同步用各圖內部標籤呈現數值，不需要全域 tooltip
}

/** 清掉群組內所有圖的同步游標（滑鼠離開群組時）。 */
export function clearSyncGroup(container) {
  container.querySelectorAll('.chart-svg').forEach(clearSyncCursor);
}

/**
 * 找出指標下方最相關的命中資料並顯示 tooltip／同步游標；找不到則隱藏。
 * 離散模式（無連續軸，如心率區間）：target 本身是帶 data-tip 的 .hit，
 *   直接顯示單圖 tooltip。
 * 連續模式：若 SVG 屬於某個同步群組，觸發整組同步；否則退回單圖 tooltip
 *   （目前 6 張圖中連續模式的都在群組內，這個分支是防禦性後備路徑）。
 */
export function handlePointerMove(evt) {
  var svg = evt.target.closest ? evt.target.closest('.chart-svg') : null;
  // ⚠️ 指標離開圖表區域（不論是離開整個委派容器，或只是移到同一圖表的
  // 座標軸標籤／圖表間的留白）都要清掉同步游標，不能只清全域 tooltip——
  // 否則游標線會殘留在畫面上直到下次進入另一個群組才被覆蓋掉。
  if (!svg) { hideAllSyncCursors(); return; }

  if (evt.target.classList && evt.target.classList.contains('hit')) {
    var meta = evt.target.__chartHitMeta;
    if (meta) {
      var container = findSyncGroupContainer(svg);
      if (container) {
        updateSyncGroup(container, svg, meta, evt.clientX, evt.clientY);
      } else {
        var pt = toSvgPoint(svg, evt.clientX, evt.clientY);
        if (!pt) { hideAllSyncCursors(); return; }
        var idx = nearestIndex(meta.xs, pt.x);
        if (meta.crosshair) {
          // 不在同步群組裡的多系列圖：在圖內畫垂直參考線與多列數值標籤
          if (loneCursorSvg && loneCursorSvg !== svg) clearSyncCursor(loneCursorSvg);
          drawSyncCursorOnSvg(svg, meta, meta.logicalXs[idx]);
          loneCursorSvg = svg;
          hideTooltip();
        } else {
          showTooltipText(tipToText(meta.tips[idx]), evt.clientX, evt.clientY);
        }
      }
      return;
    }
    var tip = evt.target.getAttribute('data-tip');
    if (tip) {
      showTooltipText(tip, evt.clientX, evt.clientY);
      return;
    }
  }
  hideAllSyncCursors();
}

/** 滑鼠離開整個委派容器時：隱藏 tooltip 並清掉所有群組的同步游標。 */
// 不屬於任何同步群組、但自己畫了參考線的圖（meta.crosshair），離開時要單獨清掉
var loneCursorSvg = null;

export function hideAllSyncCursors() {
  hideTooltip();
  if (loneCursorSvg) { clearSyncCursor(loneCursorSvg); loneCursorSvg = null; }
  SYNC_GROUPS.forEach(function (id) {
    var container = $(id);
    if (container) clearSyncGroup(container);
  });
}

/**
 * 事件委派只綁一次（在 init() 呼叫），resize 造成的整批重繪不需要重綁。
 * 用 Pointer Events 讓滑鼠與觸控共用同一套邏輯：
 *   pointermove  → 滑鼠 hover 顯示/更新 tooltip 與同步游標
 *   pointerdown  → 觸控裝置的「點一下顯示」（沒有 hover 概念）
 *   pointerleave → 滑鼠離開整個委派容器時隱藏
 *
 * ⚠️ 觸控裝置 tap 結束後緊接著會觸發 pointerleave（target 為 null，
 * 代表指標已離開追蹤範圍）——這是瀏覽器對觸控互動的正常行為，不是
 * 使用者主動把手指移開。實測（Playwright 觸控模擬）確認：tap 一個
 * .hit 矩形後，事件序列是 pointerdown → pointerup → pointerleave，
 * 若 pointerleave 無條件呼叫 hideTooltip，剛顯示的 tooltip 會在同一
 * frame 內被立即關閉，等於「點一下顯示」完全失效。故 pointerleave
 * 只在滑鼠（非觸控）時才隱藏；觸控裝置改成點擊頁面其他地方（非
 * .hit）時才隱藏，讓 tooltip 停留到使用者主動點開下一個資料點或點別處。
 */
export function bindTooltipDelegation() {
  document.body.addEventListener('pointermove', function (evt) {
    if (evt.pointerType === 'touch') return; // 觸控用 pointerdown 顯示，避免滑動時一直跳
    handlePointerMove(evt);
  });
  document.body.addEventListener('pointerdown', function (evt) {
    if (evt.pointerType !== 'touch') return;
    var isHit = evt.target.classList && evt.target.classList.contains('hit');
    if (isHit) { handlePointerMove(evt); } else { hideAllSyncCursors(); }
  });
  document.body.addEventListener('pointerleave', function (evt) {
    if (evt.pointerType === 'touch') return; // 觸控的「離開」是 tap 結束的正常訊號，不應隱藏
    hideAllSyncCursors();
  });
}
