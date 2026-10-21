/**
 * settings.js — 設定 view：同步狀態與 AI 教練面板預設開合
 */

import * as Theme from '../theme.js';
import { $ } from '../core/dom.js';
import { renderSyncFreshness } from '../shell/sync.js';
import { setCoachPanel } from '../shell/coach-frame.js';

export function renderSettingsSync() {
  renderSyncFreshness('settings-sync', false);
}

/**
 * 「面板預設開啟」開關。與面板目前的開合狀態共用 settings.coachPanel
 * 一個欄位（§4.2 指定），所以兩邊必須雙向連動：
 *   - 從設定頁切換 → 立刻套用到面板，否則開關說「預設開啟」但面板沒反應
 *   - 從面板收合／展開 → 由 setCoachPanel 呼叫 syncCoachSettingToggle 回寫勾選狀態
 * 只用一個欄位而不另開「預設值」欄位，是因為使用者最後一次的開合本來就是
 * 他要的預設；分成兩個欄位反而會出現「設定說開、實際記住關」的矛盾。
 */
export function bindCoachSetting() {
  var toggle = $('setting-coach-open');
  if (!toggle) return;
  syncCoachSettingToggle();
  toggle.addEventListener('change', function () {
    setCoachPanel(toggle.checked, true);
  });
}

export function syncCoachSettingToggle() {
  var toggle = $('setting-coach-open');
  if (!toggle) return;
  var settings = Theme.getSettings();
  toggle.checked = !(settings && settings.coachPanel === 'collapsed');
}
