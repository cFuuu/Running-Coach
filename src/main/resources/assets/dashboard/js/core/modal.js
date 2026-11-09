/**
 * modal.js — 共用大視窗（N-41 起）：放不進 ⓘ 小視窗（340px、捲動就關）的長內容。
 *
 * 桌機置中（最寬約 720px）、暗背景；手機（≤767px）為底部滑出、幾乎全螢幕。
 * 關閉：✕、Esc、點背景。內容區自己捲動、捲動不關；開著時鎖住頁面捲動。
 * 開啟時焦點移進視窗、Tab 只在視窗內循環，關閉後還給觸發的元素。
 * 同一時間只有一個（開新的會先關舊的）。
 */

import { h } from './dom.js';

var current = null;
var titleSeq = 0;

var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function onKeydown(evt) {
  if (!current) return;
  if (evt.key === 'Escape') {
    evt.preventDefault();
    closeModal();
    return;
  }
  if (evt.key !== 'Tab') return;
  var items = Array.prototype.filter.call(current.dialog.querySelectorAll(FOCUSABLE), function (el) {
    return el.offsetParent !== null; // 排除 display:none 的（例如桌機隱藏的分頁鈕）
  });
  if (!items.length) { evt.preventDefault(); return; }
  var first = items[0];
  var last = items[items.length - 1];
  var active = document.activeElement;
  if (evt.shiftKey && (active === first || !current.dialog.contains(active) || active === current.dialog)) {
    evt.preventDefault();
    last.focus();
  } else if (!evt.shiftKey && (active === last || !current.dialog.contains(active))) {
    evt.preventDefault();
    first.focus();
  }
}

/**
 * 開大視窗。
 * @param {Object} opts
 *   title：標題文字
 *   className：加在視窗本體上的 class（各功能自己的版面）
 *   returnFocus：關閉後焦點要還給的元素；省略時用開啟當下的焦點元素
 *   onClose：關閉後呼叫
 * @returns {{dialog: HTMLElement, body: HTMLElement, close: function, isOpen: function}}
 *   body 是捲動區，內容由呼叫端放入。
 */
export function openModal(opts) {
  closeModal(false);
  var restoreTo = opts.returnFocus || document.activeElement;

  var backdrop = h('div', 'modal-backdrop');
  var dialog = h('div', 'modal' + (opts.className ? ' ' + opts.className : ''));
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.tabIndex = -1;
  var titleId = 'modal-title-' + (++titleSeq);
  dialog.setAttribute('aria-labelledby', titleId);

  var head = h('div', 'modal-head');
  var title = h('h2', 'modal-title', opts.title);
  title.id = titleId;
  head.appendChild(title);
  var close = h('button', 'modal-close', '✕');
  close.type = 'button';
  close.setAttribute('aria-label', '關閉' + (opts.title || ''));
  close.addEventListener('click', function () { closeModal(); });
  head.appendChild(close);
  dialog.appendChild(head);

  var body = h('div', 'modal-body');
  dialog.appendChild(body);
  backdrop.appendChild(dialog);
  // 只有點在背景本身才關（點視窗內、或在視窗內按下後拖到背景放開都不算）
  var downOnBackdrop = false;
  backdrop.addEventListener('pointerdown', function (evt) { downOnBackdrop = evt.target === backdrop; });
  backdrop.addEventListener('click', function (evt) {
    if (evt.target === backdrop && downOnBackdrop) closeModal();
  });

  document.body.appendChild(backdrop);
  document.documentElement.classList.add('modal-open');
  document.addEventListener('keydown', onKeydown);

  var entry = { backdrop: backdrop, dialog: dialog, restoreTo: restoreTo, onClose: opts.onClose };
  current = entry;
  dialog.focus({ preventScroll: true });
  return {
    dialog: dialog,
    body: body,
    close: function () { if (current === entry) closeModal(); },
    // 非同步載入完成時先問一聲：使用者可能已經關掉或開了別的視窗
    isOpen: function () { return current === entry; }
  };
}

/** @param {boolean} [restoreFocus] 預設 true：焦點還給開啟前的元素 */
export function closeModal(restoreFocus) {
  if (!current) return;
  var st = current;
  current = null;
  document.removeEventListener('keydown', onKeydown);
  st.backdrop.remove();
  document.documentElement.classList.remove('modal-open');
  if (restoreFocus !== false && st.restoreTo && st.restoreTo.isConnected && st.restoreTo.focus) {
    st.restoreTo.focus({ preventScroll: true });
  }
  if (st.onClose) st.onClose();
}
