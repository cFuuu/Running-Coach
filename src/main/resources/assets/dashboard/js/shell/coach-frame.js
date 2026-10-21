/**
 * coach-frame.js — AI 教練面板的開合（桌機欄位／平板抽屜／手機全螢幕）與視窗縮放重繪
 */

import * as Theme from '../theme.js';
import { state } from '../core/state.js';
import { $ } from '../core/dom.js';
import { renderSessionDetail } from '../views/sessions.js';
import { renderLoad, renderTrends } from '../views/load.js';
import { renderWellness } from '../views/wellness.js';
import { renderOverview } from '../views/overview.js';
import { syncCoachSettingToggle } from '../views/settings.js';

/**
 * 設定面板展開狀態。
 * @param {boolean} open
 * @param {boolean} [persist] 是否寫入偏好（程式啟動時的還原不需重寫）
 */
export function setCoachPanel(open, persist) {
  document.body.classList.toggle('coach-open', open);
  var panel = $('coach-panel');
  var rail = $('coach-rail');
  var scrim = $('coach-scrim');
  var toggle = $('coach-toggle');
  if (panel) panel.setAttribute('aria-hidden', open ? 'false' : 'true');
  if (rail) rail.hidden = open;
  if (scrim) scrim.hidden = !open;
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (persist) {
    Theme.saveSettings({ coachPanel: open ? 'open' : 'collapsed' });
    syncCoachSettingToggle(); // 設定頁的開關與面板共用同一個欄位，不可各記一份
  }
}

/** 供 coach.js 的 runAnalysis()／showAnalyses() 打開面板（不寫入偏好：這是一次性動作）。 */
export function openCoachPanel() {
  setCoachPanel(true, false);
}

export function bindCoachPanel() {
  var settings = Theme.getSettings();
  // 桌機預設展開（三欄常駐是本次改版的重點之一），只有明確收合過才記住收合。
  // 平板／手機的面板是覆蓋在內容上的抽屜／全螢幕 sheet，預設展開會把整個
  // 儀表板蓋掉，所以一律預設收起、由使用者按開關或 FAB 開啟。
  var wide = window.innerWidth >= 1280;
  setCoachPanel(wide && !(settings && settings.coachPanel === 'collapsed'), false);

  [['coach-collapse', false], ['coach-close', false], ['coach-rail', true],
   ['coach-fab', true], ['coach-scrim', false]].forEach(function (pair) {
    var el = $(pair[0]);
    if (el) el.addEventListener('click', function () { setCoachPanel(pair[1], true); });
  });

  var toggle = $('coach-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      setCoachPanel(!document.body.classList.contains('coach-open'), true);
    });
  }

  // 平板／手機的覆蓋層形態下，Esc 關閉面板
  document.addEventListener('keydown', function (evt) {
    if (evt.key !== 'Escape') return;
    if (window.innerWidth >= 1280) return;
    if (document.body.classList.contains('coach-open')) setCoachPanel(false, true);
  });
}

// 視窗尺寸改變時重繪（X 軸刻度數量依容器寬度決定，需重算）
// 只重繪目前 view——隱藏的 view 寬度為 0，畫了也是空的，改標髒等切過去再畫。
export var resizeTimer = null;
export function bindResize() {
  window.addEventListener('resize', function () {
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (state.view === 'overview') {
        renderOverview();
      } else if (state.view === 'sessions') {
        if (state.sessionDetail) renderSessionDetail();
      } else if (state.view === 'load') {
        if (state.loadSeries) renderLoad();
        if (state.sessions.length > 0) renderTrends();
      } else if (state.view === 'wellness') {
        if (state.wellness) renderWellness();
      }
      // 非目前 view 的圖表在隱藏狀態下寬度為 0，畫了也是空的：標髒等切過去再畫
      state.dirty.overview = state.dirty.overview || state.view !== 'overview';
      state.dirty.sessions = state.dirty.sessions || state.view !== 'sessions';
      state.dirty.load = state.dirty.load || state.view !== 'load';
      state.dirty.wellness = state.dirty.wellness || state.view !== 'wellness';

      // 縮到 1280px 以下時，常駐欄變成覆蓋式抽屜／sheet：若維持展開會蓋住
      // 內容，故自動收起（不寫入偏好——這是版面因應，不是使用者的選擇）。
      if (window.innerWidth < 1280 && document.body.classList.contains('coach-open')) {
        setCoachPanel(false, false);
      }
    }, 200);
  });
}
