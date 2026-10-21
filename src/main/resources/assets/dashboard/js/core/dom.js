/**
 * dom.js — DOM 小工具：取元素、建元素、清空，以及載入中／錯誤／無資料的共用呈現
 */
export function $(id) { return document.getElementById(id); }

export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

/** 建立元素 */
export function h(tag, className, textContent) {
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (textContent !== undefined && textContent !== null) node.textContent = textContent;
  return node;
}

/** 在容器內顯示「載入中」 */
export function showLoading(node, label) {
  clear(node);
  node.appendChild(h('div', 'state-msg state-loading', (label || '載入中') + '…'));
}

/** 在容器內顯示錯誤 */
export function showError(node, message) {
  clear(node);
  var box = h('div', 'state-msg state-error');
  box.appendChild(h('strong', null, '讀取失敗'));
  box.appendChild(h('span', null, message));
  node.appendChild(box);
}

/**
 * 顯示 available:false 的說明（直接呈現 API 回傳的 reason，不畫空圖）。
 */
export function showUnavailable(node, reason) {
  clear(node);
  node.appendChild(h('div', 'state-msg state-unavailable', reason || '此項目無資料'));
}

/** 建立一張數字卡 */
export function statCard(label, value, sub) {
  var card = h('div', 'stat-card');
  card.appendChild(h('div', 'stat-label', label));
  card.appendChild(h('div', 'stat-value', value));
  if (sub) card.appendChild(h('div', 'stat-sub', sub));
  return card;
}

/**
 * 取容器實際像素寬（供圖表決定 X 軸刻度數量）。
 */
export function widthOf(node) {
  var w = node && node.clientWidth ? node.clientWidth : 0;
  return w > 0 ? w : (window.innerWidth || 360);
}

/** 把 SVG 放進一個可自行橫向捲動的包裝（整頁 body 不會橫向捲動） */
export function wrapChart(svg) {
  var wrap = h('div', 'chart-wrap');
  wrap.appendChild(svg);
  return wrap;
}
