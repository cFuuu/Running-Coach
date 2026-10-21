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
 * 【排序用上下箭頭而非拖曳】HTML5 drag&drop 在行動瀏覽器無效（觸控不觸發
 * drag 事件），自製觸控拖曳成本遠超單人自用工具的需求；箭頭鍵盤可操作、
 * 觸控可點擊，而且資料模型（有序 key 陣列）與互動層獨立，日後要換也不用改儲存格式。
 */

import * as Theme from '../theme.js';
import { h } from './dom.js';

// 哪些自訂面板目前是開著的。呼叫端調整後通常整塊 clear() 重畫（連同自訂鈕），
// 重建時依這裡自動展開，連續調整多個項目才不必每次重新點開。
var openKeys = {};

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
  layout.ordered.forEach(function (def, idx) {
    var row = h('label', 'customize-row');
    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !hidden[def.key];
    checkbox.addEventListener('change', function () {
      if (checkbox.checked) delete hidden[def.key]; else hidden[def.key] = true;
      commit(keys.slice());
    });
    row.appendChild(checkbox);
    row.appendChild(h('span', 'customize-label', def.label));

    function moveBtn(text, delta, aria) {
      var btn = h('button', 'customize-move', text);
      btn.type = 'button';
      btn.setAttribute('aria-label', def.label + aria);
      var target = idx + delta;
      btn.disabled = target < 0 || target >= keys.length;
      btn.addEventListener('click', function (evt) {
        evt.preventDefault(); // 在 <label> 內，避免點按鈕連帶切換勾選
        var order = keys.slice();
        var tmp = order[target]; order[target] = order[idx]; order[idx] = tmp;
        commit(order);
      });
      return btn;
    }
    row.appendChild(moveBtn('↑', -1, '上移'));
    row.appendChild(moveBtn('↓', 1, '下移'));
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
  return wrap;
}
