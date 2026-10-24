/**
 * coach-frame.js — AI 教練面板的開合（桌機欄位／平板抽屜／手機全螢幕）、分析按鈕與視窗縮放重繪
 */

import * as Theme from '../theme.js';
import { state } from '../core/state.js';
import { $, clear, h } from '../core/dom.js';
import { isOfflineSnapshot } from '../core/api.js';
import { syncCoachSettingToggle } from '../views/settings.js';
import { renderDirtyView } from './router.js';
import { runAnalysis, showAnalyses } from '../coach.js';

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
  // 離線快照：面板與開關都隱藏（[data-online-only]），維持收起，不綁任何開合事件
  if (isOfflineSnapshot()) { setCoachPanel(false, false); return; }
  var settings = Theme.getSettings();
  // 桌機預設展開（三欄常駐是本次改版的重點之一），只有明確收合過才記住收合。
  // 平板／手機的面板是覆蓋在內容上的抽屜／全螢幕 sheet，預設展開會把整個
  // 儀表板蓋掉，所以一律預設收起、由使用者按開關或 FAB 開啟。
  var wide = window.innerWidth >= 1280;
  setCoachPanel(wide && !(settings && settings.coachPanel === 'collapsed'), false);

  [['coach-collapse', false], ['coach-close', false], ['coach-rail', true],
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
 * 面板標題列的「分析近況」（跑一次新的 AI 分析）與「分析紀錄」（回看過去的報告）。
 * 進度與結果都在 AI 面板裡呈現；無法開始時（例如未設定 API key）把原因放在
 * 內容頂端的提示列，按鈕旁沒有空間放說明文字。
 */
export function bindAnalysisButtons() {
  var btn = $('analyze-btn');
  if (btn) {
    btn.addEventListener('click', function () {
      btn.disabled = true;
      clear($('notice-bar'));
      runAnalysis().catch(function (err) {
        var notice = $('notice-bar');
        notice.appendChild(h('span', 'state-error-inline',
          '無法開始分析：' + (err && err.message ? err.message : '原因不明')));
      }).then(function () { btn.disabled = false; });
    });
  }
  var history = $('coach-history');
  if (history) history.addEventListener('click', function () { showAnalyses(); });
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
