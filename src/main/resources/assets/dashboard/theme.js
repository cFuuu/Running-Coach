/**
 * theme.js — 主題（淺色／深色／跟隨系統）切換 + 使用者設定的統一讀寫入口
 *
 * 【架構角色】
 *   本檔是 localStorage key `rc-dashboard-settings` 的唯一讀寫入口。
 *   之後 Phase 4（自訂時間區間）、Phase 5（指標排序／顯隱）都會往同一個
 *   物件加欄位，不另開新 key——避免使用者的偏好設定散落在多個 key 造成
 *   讀取順序／版本判斷的混亂。
 *
 *   index.html <head> 內有一段 inline script 會搶在本檔之前，先讀一次
 *   localStorage 把 data-theme 設好以避免 FOUC；兩處讀取的 key 與判斷邏輯
 *   必須一致（inline script 只認 theme 欄位，本檔是完整版本）。
 *
 * 【對外介面】window.Theme
 *   getSettings() / saveSettings(patch)  — 設定的讀寫（自動處理版本與防禦性解析）
 *   init()                                — 綁定切換鈕、套用初始主題、監聽系統主題變化
 */
window.Theme = (function () {
  'use strict';

  var STORAGE_KEY = 'rc-dashboard-settings';
  var SETTINGS_VERSION = 1;
  var VALID_THEME_CHOICES = ['light', 'dark', 'system'];

  function defaults() {
    return { v: SETTINGS_VERSION, theme: 'system' };
  }

  /**
   * 讀取使用者設定。版本不符或內容損毀一律丟棄回預設值，
   * 不嘗試遷移欄位——單人自用工具，防禦性優先於相容性。
   */
  function getSettings() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return defaults();
      var parsed = JSON.parse(raw);
      if (!parsed || parsed.v !== SETTINGS_VERSION) return defaults();
      return parsed;
    } catch (e) {
      return defaults();
    }
  }

  /**
   * 淺層合併並寫回。呼叫端只需傳要改的欄位。
   * @param {Object} patch
   */
  function saveSettings(patch) {
    var current = getSettings();
    var next = {};
    Object.keys(current).forEach(function (k) { next[k] = current[k]; });
    Object.keys(patch).forEach(function (k) { next[k] = patch[k]; });
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch (e) {
      // 私密瀏覽模式或容量滿載：靜默放棄持久化，不影響當次頁面行為
    }
  }

  function systemPrefersDark() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function resolve(choice) {
    return choice === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : choice;
  }

  function apply(choice) {
    document.documentElement.setAttribute('data-theme', resolve(choice));
    updateButtons(choice);
  }

  function updateButtons(choice) {
    var btns = document.querySelectorAll('.theme-btn');
    for (var i = 0; i < btns.length; i += 1) {
      var isActive = btns[i].getAttribute('data-theme-choice') === choice;
      btns[i].setAttribute('aria-pressed', isActive ? 'true' : 'false');
    }
  }

  function setChoice(choice) {
    if (VALID_THEME_CHOICES.indexOf(choice) === -1) return;
    saveSettings({ theme: choice });
    apply(choice);
  }

  function bindButtons() {
    var btns = document.querySelectorAll('.theme-btn');
    for (var i = 0; i < btns.length; i += 1) {
      btns[i].addEventListener('click', (function (btn) {
        return function () { setChoice(btn.getAttribute('data-theme-choice')); };
      })(btns[i]));
    }
  }

  /**
   * 系統主題變化時，只有目前選的是「跟隨系統」才需要即時反應；
   * 若使用者已明確選了淺色／深色，系統變化不該覆蓋使用者的選擇。
   */
  function bindSystemChange() {
    if (!window.matchMedia) return;
    var mql = window.matchMedia('(prefers-color-scheme: dark)');
    var handler = function () {
      if (getSettings().theme === 'system') apply('system');
    };
    if (mql.addEventListener) mql.addEventListener('change', handler);
    else if (mql.addListener) mql.addListener(handler); // 舊瀏覽器 fallback
  }

  function init() {
    apply(getSettings().theme);
    bindButtons();
    bindSystemChange();
  }

  return {
    getSettings: getSettings,
    saveSettings: saveSettings,
    init: init
  };
})();
