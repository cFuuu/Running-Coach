/**
 * range.js — 時間區間切換：range 按鈕、自訂區間面板、網址與偏好同步
 */

import * as Theme from '../theme.js';
import { state, FALLBACK_RANGES, CUSTOM_RANGE_PREFIX, isValidRangeKey } from '../core/state.js';
import { $, clear, h } from '../core/dom.js';
import { loadRangeData } from './loader.js';
import { navigate } from './router.js';

/**
 * 設定時間區間並重新載入。
 * @param {string} range '7d'|'30d'|'90d'|'1y'|'all'，或自訂區間
 *   'custom:YYYY-MM-DD:YYYY-MM-DD'（見後端 dashboard_queries.CUSTOM_RANGE_PREFIX）
 */
export function setRange(range) {
  if (!isValidRangeKey(range)) return;
  if (state.range === range) return;
  state.range = range;
  saveRangeToSettings(range);
  syncRangeToUrl(range);
  updateRangeButtons();
  loadRangeData();
}

export function saveRangeToSettings(range) {
  Theme.saveSettings({ range: range });
}

/**
 * 把目前的 range 同步進網址 query（用 replaceState，不新增瀏覽紀錄，
 * 避免每切一次 range 使用者按上一頁就要按很多次）。讓 URL 可以直接
 * 分享／加書籤，開啟時由 restoreInitialRange() 讀回。
 */
export function syncRangeToUrl(range) {
  if (!window.history || !window.history.replaceState) return;
  var url = new URL(window.location.href);
  url.searchParams.set('range', range);
  window.history.replaceState(null, '', url.toString());
}

/**
 * 選定場次並重新載入該場詳細資料。
 * @param {number} id
 */
export function selectSession(id) {
  if (id === null || id === undefined || isNaN(id)) return;
  navigate('sessions', id);
}

export function updateRangeButtons() {
  var buttons = document.querySelectorAll('#range-switch .range-btn');
  var isCustom = state.range.indexOf(CUSTOM_RANGE_PREFIX) === 0;
  Array.prototype.forEach.call(buttons, function (btn) {
    var active = btn.getAttribute('data-range') === state.range;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  var toggle = $('custom-range-toggle');
  if (toggle) toggle.classList.toggle('is-active', isCustom);
}

/**
 * 依 /api/meta 回傳的 ranges 動態產生按鈕（後端 RANGE_LABELS 是唯一真實
 * 來源）。找不到 meta.ranges 時退回 FALLBACK_RANGES 的固定標籤，確保
 * meta 請求失敗時使用者仍能操作預設區間。
 */
export function renderRangeButtons() {
  var container = $('range-switch');
  if (!container) return;
  clear(container);
  var ranges = (state.meta && state.meta.ranges) || FALLBACK_RANGES.map(function (key) {
    return { key: key, label: key };
  });
  ranges.forEach(function (r) {
    var btn = h('button', 'range-btn', r.label);
    btn.type = 'button';
    btn.setAttribute('data-range', r.key);
    btn.setAttribute('aria-pressed', r.key === state.range ? 'true' : 'false');
    btn.addEventListener('click', function () { setRange(r.key); });
    container.appendChild(btn);
  });
  updateRangeButtons();
}

/**
 * 自訂區間面板：輸入 min/max 依 meta.data_bounds 限制在實際有資料的
 * 範圍內，避免選到必然空白的區間。套用時關閉面板並呼叫既有的 setRange()
 * 單一入口——自訂區間與預設區間共用同一條「設定 range → 重抓 → 重繪」
 * 路徑，不另開一條。
 */
export function bindCustomRangePanel() {
  var toggle = $('custom-range-toggle');
  var panel = $('custom-range-panel');
  var startInput = $('custom-range-start');
  var endInput = $('custom-range-end');
  var applyBtn = $('custom-range-apply');
  if (!toggle || !panel || !startInput || !endInput || !applyBtn) return;

  function closePanel() {
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }
  function openPanel() {
    var bounds = state.meta && state.meta.data_bounds;
    if (bounds) {
      startInput.min = bounds.earliest_date;
      startInput.max = bounds.latest_date;
      endInput.min = bounds.earliest_date;
      endInput.max = bounds.latest_date;
    }
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
  }

  toggle.addEventListener('click', function () {
    if (panel.hidden) openPanel(); else closePanel();
  });
  applyBtn.addEventListener('click', function () {
    if (!startInput.value || !endInput.value) return;
    if (startInput.value > endInput.value) return; // 起訖顛倒，交給使用者自行修正，不代為調換
    setRange(CUSTOM_RANGE_PREFIX + startInput.value + ':' + endInput.value);
    closePanel();
  });
  // 點擊面板外部收合（但不吃掉面板內部的點擊，也不影響其他委派事件）
  document.addEventListener('click', function (evt) {
    var container = $('custom-range');
    if (container && !container.contains(evt.target)) closePanel();
  });
}

//
// hash 路由 `#/view[/arg]`：
//   #/today  #/calendar  #/calendar/2026-09  #/sessions  #/sessions/1083
//   #/load   #/wellness
// 五個 view 是 <section class="view" hidden>，切換只動 hidden——不重建 DOM，
// 圖表與既有容器 id 都原地保留。
//
// 為什麼不在切換時無條件重畫：widthOf() 對 display:none 的容器回 0，
// 隱藏 view 內畫出來的 SVG 寬度會是 0。所以資料載入只渲染「目前」view，
// 其餘標髒（state.dirty），切過去時才補畫。
