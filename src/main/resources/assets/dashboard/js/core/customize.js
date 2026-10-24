/**
 * customize.js — 「排序＋顯隱」的共用機制：偏好存讀與「⚙ 自訂」面板
 *
 * 首頁區塊、趨勢頁類別與各類別的圖表、身體狀況指標都用這一套。
 *
 * 【儲存】一律存在 theme.js 的 `rc-dashboard-settings`（不另開 localStorage key），
 * 以 settingsKey 區分：settings[settingsKey] = { order: [key…], hidden: [key…] }。
 * 身體狀況沿用既有的 'wellness'，使用者原本的設定因此不會遺失。
 *
 * 【為什麼存「順序＋隱藏清單」而不是「可見清單」】
 * 之後新增的項目不在使用者存過的 order 裡，會排到末端自動出現，而不是被舊
 * 設定永久藏起來（使用者會以為新功能沒生效）。已不存在的舊 key 直接忽略。
 * hidden 為 null（從未動過設定）時，用各項目的 default_hidden 決定初始顯隱。
 *
 * 【排序用拖移把手（Pointer Events），不用 HTML5 drag&drop】後者在行動瀏覽器無效
 * （觸控不觸發 drag 事件）。Pointer Events 滑鼠與觸控共用，把手設 touch-action:none
 * 就不會被頁面捲動搶走；把手聚焦後也能按 ↑／↓ 移動，鍵盤可操作。
 * 資料模型（有序 key 陣列）與互動層獨立，儲存格式沒有因此改變。
 * （2026-10-23 由上下箭頭鈕改為拖移，原因：箭頭一次只動一格、清單長時很難用。）
 */

import * as Theme from '../theme.js';
import { h } from './dom.js';

// 哪些自訂面板目前是開著的。呼叫端調整後通常整塊 clear() 重畫（連同自訂鈕），
// 重建時依這裡自動展開，連續調整多個項目才不必每次重新點開。
var openKeys = {};
// 每個 settingsKey 最新一個自訂鈕的 { wrap, setOpen }，讓系統設定頁的捷徑能從外部展開它
var openers = {};

/**
 * 依使用者偏好排出項目。
 * @param {string} settingsKey
 * @param {Array} defs [{ key, label, default_hidden? }]，原始（預設）順序
 * @returns {{ ordered: Array, hidden: Object }} ordered 含隱藏項目；hidden 為 key → true
 */
export function layoutOf(settingsKey, defs) {
  var byKey = {};
  defs.forEach(function (d) { byKey[d.key] = d; });
  var saved = Theme.getSettings()[settingsKey] || {};
  var order = Array.isArray(saved.order) ? saved.order : [];
  var hiddenList = Array.isArray(saved.hidden) ? saved.hidden : null;

  var ordered = [];
  order.forEach(function (key) {
    if (byKey[key]) { ordered.push(byKey[key]); delete byKey[key]; }
  });
  defs.forEach(function (d) { if (byKey[d.key]) ordered.push(d); }); // 新項目排末端

  var hidden = {};
  ordered.forEach(function (d) {
    var isHidden = hiddenList ? hiddenList.indexOf(d.key) !== -1 : !!d.default_hidden;
    if (isHidden) hidden[d.key] = true;
  });
  return { ordered: ordered, hidden: hidden };
}

/** 依使用者偏好排序、且未被隱藏的項目。 */
export function visibleDefs(settingsKey, defs) {
  var layout = layoutOf(settingsKey, defs);
  return layout.ordered.filter(function (d) { return !layout.hidden[d.key]; });
}

function saveLayout(settingsKey, order, hidden) {
  var patch = {};
  patch[settingsKey] = { order: order, hidden: order.filter(function (k) { return hidden[k]; }) };
  Theme.saveSettings(patch);
}

function buildPanel(opts) {
  var panel = h('div', 'customize-panel');
  if (opts.hint) panel.appendChild(h('p', 'customize-hint', opts.hint));
  var layout = layoutOf(opts.settingsKey, opts.defs);
  var keys = layout.ordered.map(function (d) { return d.key; });
  var hidden = layout.hidden;

  function commit(order) {
    saveLayout(opts.settingsKey, order, hidden);
    opts.onChange();
  }

  var list = h('div', 'customize-list');

  /** 依畫面上目前的列順序讀回 key 陣列（拖移中列直接在 DOM 裡移動，放開才存）。 */
  function currentOrder() {
    return Array.prototype.map.call(list.children, function (r) { return r.getAttribute('data-key'); });
  }

  /**
   * 拖移把手：用 Pointer Events（滑鼠與觸控共用；把手設 touch-action:none，
   * 觸控才不會被頁面捲動搶走）。拖動時列隨指標在清單內換位，放開才寫入偏好。
   * 把手也能用鍵盤：聚焦後按 ↑／↓ 移動一格。
   */
  function dragHandle(def, row) {
    var handle = h('button', 'customize-handle', '⠿');
    handle.type = 'button';
    handle.title = '拖移調整順序';
    handle.setAttribute('aria-label', def.label + '：拖移調整順序，或按上下鍵');
    handle.setAttribute('data-ckey', opts.settingsKey + ':' + def.key);

    handle.addEventListener('pointerdown', function (evt) {
      if (evt.pointerType === 'mouse' && evt.button !== 0) return;
      evt.preventDefault();
      handle.setPointerCapture(evt.pointerId);
      row.classList.add('is-dragging');
      var startOrder = currentOrder().join('|');

      function onMove(e) {
        // 找出「中線在指標下方」的第一個其他列，把被拖的列插到它前面；沒有就放最後
        var before = null;
        var others = Array.prototype.filter.call(list.children, function (r) { return r !== row; });
        for (var i = 0; i < others.length; i += 1) {
          var rect = others[i].getBoundingClientRect();
          if (e.clientY < rect.top + rect.height / 2) { before = others[i]; break; }
        }
        if (before !== row.nextSibling) list.insertBefore(row, before);
      }
      function onEnd() {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onEnd);
        handle.removeEventListener('pointercancel', onEnd);
        row.classList.remove('is-dragging');
        if (currentOrder().join('|') !== startOrder) commit(currentOrder());
      }
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onEnd);
      handle.addEventListener('pointercancel', onEnd);
    });

    handle.addEventListener('keydown', function (evt) {
      var delta = evt.key === 'ArrowUp' ? -1 : (evt.key === 'ArrowDown' ? 1 : 0);
      if (!delta) return;
      evt.preventDefault();
      var order = currentOrder();
      var idx = order.indexOf(def.key);
      var target = idx + delta;
      if (target < 0 || target >= order.length) return;
      var tmp = order[target]; order[target] = order[idx]; order[idx] = tmp;
      commit(order);
      // 面板會整個重畫，把焦點還給同一個把手，才能連續按鍵
      setTimeout(function () {
        var again = document.querySelector('[data-ckey="' + opts.settingsKey + ':' + def.key + '"]');
        if (again) again.focus();
      }, 0);
    });
    return handle;
  }

  layout.ordered.forEach(function (def) {
    var row = h('label', 'customize-row');
    row.setAttribute('data-key', def.key);
    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !hidden[def.key];
    checkbox.addEventListener('change', function () {
      if (checkbox.checked) delete hidden[def.key]; else hidden[def.key] = true;
      commit(keys.slice());
    });
    row.appendChild(checkbox);
    row.appendChild(h('span', 'customize-label', def.label));

    row.appendChild(dragHandle(def, row));
    list.appendChild(row);
  });
  panel.appendChild(list);
  return panel;
}

/**
 * 「⚙ 自訂」鈕＋可展開的排序／顯隱面板。
 * @param {Object} opts
 *   settingsKey  偏好的 key
 *   defs         [{ key, label, default_hidden? }]
 *   onChange     調整後呼叫，由呼叫端重畫（可以連同這顆鈕一起重建）
 *   label        按鈕文字，預設「⚙ 自訂」
 *   hint         面板頂端的一行說明（選填）
 * @returns {HTMLElement}
 */
export function customizer(opts) {
  var wrap = h('div', 'customize');
  var btn = h('button', 'customize-toggle', opts.label || '⚙ 自訂');
  btn.type = 'button';
  btn.setAttribute('aria-expanded', 'false');
  wrap.appendChild(btn);

  function setOpen(open) {
    var existing = wrap.querySelector('.customize-panel');
    if (existing) existing.parentNode.removeChild(existing);
    if (open) wrap.appendChild(buildPanel(opts));
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) openKeys[opts.settingsKey] = true; else delete openKeys[opts.settingsKey];
  }
  btn.addEventListener('click', function () { setOpen(!openKeys[opts.settingsKey]); });
  if (openKeys[opts.settingsKey]) setOpen(true);
  openers[opts.settingsKey] = { wrap: wrap, setOpen: setOpen };
  return wrap;
}

/**
 * 從外部展開某個自訂面板（系統設定頁的捷徑用）。面板還沒建立時只記下要展開，
 * 該頁之後重畫自訂鈕時就會自動打開。
 * @param {string} settingsKey 同 customizer 的 settingsKey
 * @returns {HTMLElement|null} 目前的自訂區塊（可用來捲到畫面內）
 */
export function openCustomizer(settingsKey) {
  openKeys[settingsKey] = true;
  var o = openers[settingsKey];
  if (!o || !document.body.contains(o.wrap)) return null;
  o.setOpen(true);
  return o.wrap;
}
