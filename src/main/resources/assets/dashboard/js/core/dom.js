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

//
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
