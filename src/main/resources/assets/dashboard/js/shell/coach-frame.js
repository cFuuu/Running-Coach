/**
 * coach-frame.js — AI 教練面板的開合（桌機欄位／平板抽屜／手機全螢幕）、分析按鈕與視窗縮放重繪
 */

import * as Theme from '../theme.js';
import { state } from '../core/state.js';
import { $ } from '../core/dom.js';
import { isOfflineSnapshot } from '../core/api.js';
import { syncCoachSettingToggle } from '../views/settings.js';
import { renderDirtyView } from './router.js';
import { runAnalysis } from '../coach.js';

// S1 只負責「殼」：三種寬度的形態（桌機常駐欄／平板抽屜／手機全螢幕 sheet）
// 與收合狀態的存讀。對話、串流、工具 chip 屬 S2／S3 的 coach.js，這裡不碰。
// 收合偏好沿用既有的 Theme.saveSettings，不另開 localStorage key。

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
  // 收合時渲染的訊息區高度為 0、捲不到底；打開後才捲到最新一則
  // （手機全螢幕從右上角打開時原本停在最舊的訊息，2026-10-27 合併驗證發現）
  var log = $('coach-log');
  if (open && log) requestAnimationFrame(function () { log.scrollTop = log.scrollHeight; });
  if (persist) {
    Theme.saveSettings({ coachPanel: open ? 'open' : 'collapsed' });
    syncCoachSettingToggle(); // 設定頁的開關與面板共用同一個欄位，不可各記一份
  }
}

/** 供 coach.js 的 runAnalysis() 打開面板（不寫入偏好：這是一次性動作）。 */
export function openCoachPanel() {
  setCoachPanel(true, false);
}

export function bindCoachPanel() {
  var settings = Theme.getSettings();
  // 桌機預設展開（三欄常駐是本次改版的重點之一），只有明確收合過才記住收合。
  // 平板／手機的面板是覆蓋在內容上的抽屜／全螢幕 sheet，預設展開會把整個
  // 儀表板蓋掉，所以一律預設收起、由使用者按開關或 FAB 開啟。
  // 離線快照的面板只能看對話紀錄，一律預設收起。
  var wide = window.innerWidth >= 1280;
  setCoachPanel(!isOfflineSnapshot() && wide && !(settings && settings.coachPanel === 'collapsed'), false);

  // 收合／關閉只留一顆 #coach-collapse（N-36 決策 8：寬螢幕 »、手機全螢幕 ×，圖示由 CSS 依寬度切換）
  [['coach-collapse', false], ['coach-rail', true],
   ['coach-scrim', false]].forEach(function (pair) {
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

/**
 * 面板標題列的「分析近況」：在目前對話跑一次 AI 分析（N-36 決策 4，「分析紀錄」已併入對話）。
 * 進度、結果與無法開始的原因都由 coach.js 顯示在訊息串裡；回覆或分析中的鎖定也由
 * coach.js 管（renderBusy），這裡只負責接上按鈕。
 */
export function bindAnalysisButtons() {
  var btn = $('analyze-btn');
  if (btn) {
    btn.addEventListener('click', function () {
      btn.disabled = true; // 等 status 回來前防連點；分析結束（含失敗）才還原
      runAnalysis().then(function () { btn.disabled = false; });
    });
  }
}

// 視窗寬度改變時重繪（X 軸刻度數量依容器寬度決定，需重算）
// 只重繪目前 view——隱藏的 view 寬度為 0，畫了也是空的，改標髒等切過去再畫。
// 只看寬度：iPhone Safari 捲動時網址列伸縮只改高度也會觸發 resize，若照樣整頁重畫，
// 慣性捲動中途 DOM 被替換、Safari 又沒有捲動錨定，畫面就會跳（2026-10-24 Fu 實機回報）。
export var resizeTimer = null;
export function bindResize() {
  var lastWidth = window.innerWidth;
  window.addEventListener('resize', function () {
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    if (resizeTimer) clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      // 有圖表的 view 全部標髒，再交給路由的補畫邏輯：目前 view 立刻重畫，
      // 其餘（隱藏中寬度為 0）等切過去再畫。首頁與行事曆沒有依寬度決定的圖表，不必重畫。
      state.dirty.sessions = true;
      state.dirty.trends = true;
      renderDirtyView(state.view);

      // 縮到 1280px 以下時，常駐欄變成覆蓋式抽屜／sheet：若維持展開會蓋住
      // 內容，故自動收起（不寫入偏好——這是版面因應，不是使用者的選擇）。
      if (window.innerWidth < 1280 && document.body.classList.contains('coach-open')) {
        setCoachPanel(false, false);
      }
    }, 200);
  });
}
