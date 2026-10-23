/**
 * facts.js — 事實呈現元件：信賴度 badge、ⓘ 定義浮層、指標卡（各 view 共用）
 */

import * as C from '../charts.js';
import { CONFIDENCE_LABEL, HRV_STATUS_MARK } from './state.js';
import { $, clear, h } from './dom.js';

/**
 * 把 data_confidence.fact() 的三欄位輸出（{value, source, confidence}）
 * 渲染成一個帶信賴度 badge 的行內元素。fact 為 null／undefined 時顯示
 * 「未評」（§7.3 硬規則：無法標記信賴度就不輸出裸數字，前端對應地
 * 不可把 null 顯示成 0 或空白，必須明確標示未評）。
 * @param {Object|null} fact {value, source, confidence:{level, biases}}
 * @param {function} formatter 把 value 轉成顯示字串
 * @param {string} [unit]
 */
export function factValueNode(fact, formatter, unit) {
  var wrap = h('span', 'today-fact-value');
  if (!fact || fact.value === null || fact.value === undefined) {
    wrap.appendChild(h('span', null, '未評'));
    var noneBadge = h('span', 'conf-badge conf-none', '無資料');
    wrap.appendChild(noneBadge);
    return wrap;
  }
  wrap.appendChild(document.createTextNode(formatter(fact.value) + (unit || '')));
  wrap.appendChild(confBadge(fact));
  return wrap;
}

/**
 * ⓘ 定義按鈕：點一下開一個小視窗顯示定義，按 ✕、Esc、點視窗外或捲動內容區
 * 就關閉。說明只講「這個數字是什麼、怎麼算的」，不寫好壞判讀。
 *
 * 用點擊而非 hover：手機沒有 hover。整頁共用同一個小視窗（#info-popover），
 * 開新的會取代舊的，不會疊出好幾個。
 * @param {string} label 指標名稱（小視窗標題，也是給螢幕閱讀器的按鈕名稱）
 * @param {string|string[]} text 定義文字；陣列時每個元素一段
 * @returns {HTMLElement} 按鈕
 */
export function infoTip(label, text) {
  var button = h('button', 'info-tip-btn');
  // 圓框＋一點一豎的線條圖示（SVG，粗細一致、不受字型影響）；內容是固定字面值
  button.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
    + '<circle class="info-ring" cx="10" cy="10" r="8"/>'
    + '<path class="info-ring" d="M10 9v5"/><circle class="info-dot" cx="10" cy="6.2" r="1.1"/></svg>';
  button.type = 'button';
  button.setAttribute('aria-label', label + '的定義');
  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', function (evt) {
    evt.stopPropagation();
    if (infoPopoverState.button === button) { closeInfoPopover(); return; }
    openInfoPopover(button, label, Array.isArray(text) ? text : [text]);
  });
  return button;
}

export var infoPopoverState = { el: null, button: null };

export function ensureInfoPopover() {
  if (infoPopoverState.el) return infoPopoverState.el;
  var el = h('div', 'info-popover');
  el.id = 'info-popover';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'false');
  el.setAttribute('aria-labelledby', 'info-popover-title');
  el.tabIndex = -1;
  el.hidden = true;
  var head = h('div', 'info-popover-head');
  var title = h('h4', 'info-popover-title');
  title.id = 'info-popover-title';
  head.appendChild(title);
  var close = h('button', 'info-popover-close', '×');
  close.type = 'button';
  close.setAttribute('aria-label', '關閉說明');
  close.addEventListener('click', function () { closeInfoPopover(); });
  head.appendChild(close);
  el.appendChild(head);
  el.appendChild(h('div', 'info-popover-body'));
  // 點視窗內部不算「點外面」
  el.addEventListener('click', function (evt) { evt.stopPropagation(); });
  document.body.appendChild(el);
  infoPopoverState.el = el;
  return el;
}

/**
 * 開小視窗並定位在按鈕下方（右緣對齊按鈕）；下方放不下就翻到上方，
 * 左右與上下都夾在視窗內 12px，窄螢幕也不會超出畫面或造成橫向捲動。
 */
export function openInfoPopover(button, label, paragraphs) {
  closeInfoPopover();
  var el = ensureInfoPopover();
  el.querySelector('.info-popover-title').textContent = label;
  var body = el.querySelector('.info-popover-body');
  clear(body);
  paragraphs.forEach(function (p) { body.appendChild(h('p', null, p)); });
  el.hidden = false;

  var margin = 12;
  var vw = document.documentElement.clientWidth;
  var vh = window.innerHeight;
  var rect = button.getBoundingClientRect();
  var width = Math.min(340, vw - margin * 2);
  el.style.width = width + 'px';
  var left = Math.min(Math.max(rect.right - width, margin), vw - width - margin);
  var height = el.offsetHeight;
  var top = rect.bottom + 6;
  if (top + height > vh - margin) top = rect.top - height - 6;
  top = Math.max(margin, Math.min(top, vh - height - margin));
  el.style.left = left + 'px';
  el.style.top = top + 'px';

  infoPopoverState.button = button;
  button.setAttribute('aria-expanded', 'true');
  el.focus({ preventScroll: true });
}

/** @param {boolean} [restoreFocus] 是否把焦點還給按鈕（Esc／✕ 關閉時要，點別處時不要） */
export function closeInfoPopover(restoreFocus) {
  var st = infoPopoverState;
  if (!st.el || st.el.hidden) return;
  st.el.hidden = true;
  if (st.button) {
    st.button.setAttribute('aria-expanded', 'false');
    if (restoreFocus) st.button.focus({ preventScroll: true });
  }
  st.button = null;
}

export function bindInfoTipDismiss() {
  document.addEventListener('click', function () { closeInfoPopover(false); });
  document.addEventListener('keydown', function (evt) {
    if (evt.key === 'Escape') closeInfoPopover(true);
  });
  // 小視窗是 fixed 定位，內容區一捲動就會和按鈕錯開，直接關掉
  var content = $('content');
  if (content) content.addEventListener('scroll', function () { closeInfoPopover(false); }, { passive: true });
  window.addEventListener('scroll', function () { closeInfoPopover(false); }, { passive: true });
  window.addEventListener('resize', function () { closeInfoPopover(false); });
}

// 內容是固定字面值，不含外部資料
var HRV_MARK_SHAPE = {
  circle: '<circle cx="7" cy="7" r="5.5"/>',
  square: '<rect x="1.8" y="1.8" width="10.4" height="10.4" rx="1.5"/>',
  triangle: '<path d="M7 1.6 12.8 12H1.2z"/>'
};

/** Garmin HRV 狀態的形狀＋顏色圖示（綠圓／橘方／紅三角）。未知狀態回傳 null。 */
export function hrvStatusMark(status) {
  var def = HRV_STATUS_MARK[status];
  if (!def) return null;
  var mark = h('span', 'hrv-mark hrv-mark-' + def.tone);
  mark.innerHTML = '<svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
    + HRV_MARK_SHAPE[def.shape] + '</svg>';
  return mark;
}

/** 信賴度 badge（fact 的三欄位契約），hover 顯示依據來源與已知偏誤。 */
export function confBadge(fact) {
  var level = (fact && fact.confidence && fact.confidence.level) || 'high';
  var badge = h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level);
  var biases = (fact && fact.confidence && fact.confidence.biases || [])
    .map(function (b) { return b.detail; }).join('；');
  badge.title = ((fact && fact.source) || '') + (biases ? '　' + biases : '');
  return badge;
}

/**
 * 一張 KPI 卡：大數字 + 單位 + 下方註記 + 右側迷你趨勢線。
 * 版面參考常見跑者儀表板的「數值配縮圖」模式，但只放事實，
 * 判讀留給 AI 教練面板（本頁頁尾明載「不含任何解讀或建議」）。
 * @param {Object} cfg { label, abbr, chip, value, unit, note, series, invert, tip, badge }
 *   abbr：白話標題後面的縮寫（例如 label「近期疲勞」、abbr「ATL」），讓不記得縮寫的人也認得出來
 *   chip：標題旁的時間範圍小標籤（例如「90 天」「本週」），讓每個數字的涵蓋範圍一目了然
 *   tip：定義文字（有給才出現 ⓘ 按鈕，見 infoTip）
 *   badge：附在數字旁的節點（信賴度 badge 等）
 */
export function kpiCard(cfg) {
  var card = h('div', 'kpi-card');
  var labelRow = h('div', 'kpi-label');
  var labelText = h('span', null, cfg.label);
  if (cfg.abbr) labelText.appendChild(h('span', 'kpi-abbr', cfg.abbr));
  if (cfg.chip) labelText.appendChild(h('span', 'window-chip', cfg.chip));
  labelRow.appendChild(labelText);
  if (cfg.tip) labelRow.appendChild(infoTip(cfg.label + (cfg.abbr ? '（' + cfg.abbr + '）' : ''), cfg.tip));
  card.appendChild(labelRow);

  var main = h('div', 'kpi-main');
  var valWrap = h('div', 'kpi-value-wrap');
  valWrap.appendChild(h('span', 'kpi-value', cfg.value));
  if (cfg.unit) valWrap.appendChild(h('span', 'kpi-unit', cfg.unit));
  if (cfg.badge) valWrap.appendChild(cfg.badge);
  main.appendChild(valWrap);

  if (cfg.series && C.sparkline) {
    var spark = C.sparkline(cfg.series, { invert: cfg.invert });
    if (spark) {
      var sparkWrap = h('div', 'kpi-spark');
      sparkWrap.appendChild(spark);
      main.appendChild(sparkWrap);
    }
  }
  card.appendChild(main);

  if (cfg.note) card.appendChild(h('div', 'kpi-note', cfg.note));
  return card;
}
