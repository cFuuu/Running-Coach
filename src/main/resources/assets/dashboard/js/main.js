/**
 * main.js — Dashboard 進入點（ES module）
 *
 * 【模組分層】
 *   core/   狀態、資料存取、DOM 小工具、格式化、tooltip、事實呈現元件——不依賴任何 view
 *   views/  各分頁的載入與繪製（home／calendar／trends 的 overview・load・wellness／sessions／settings）
 *   shell/  App 殼：路由、range 切換、載入流程、資料同步、AI 面板開合
 *   charts.js 只負責畫圖（純函式），不做 fetch、不讀寫狀態
 *   coach.js  AI 教練面板
 *
 * 【狀態切換入口】
 *   setRange() / selectSession()（shell/range.js）是「設定狀態 → 重抓 → 重繪」的
 *   單一路徑；日後接手勢／鍵盤互動時直接 import 呼叫即可，charts.js 不用改。
 *
 * 【API base URL】
 *   一律用相對路徑（後端把靜態檔掛在 /，API 在 /api/*），不寫死絕對網址。
 *
 * 【假資料】
 *   預設走真實 API。網址帶 ?mock=1 時，index.html 會在 module 執行前同步插入
 *   mock-data.js（classic script），此時所有請求改走 window.MOCK_API。
 */
import * as Theme from './theme.js';
import { state, FALLBACK_RANGES, CUSTOM_RANGE_PATTERN } from './core/state.js';
import { api } from './core/api.js';
import { $, clear, h } from './core/dom.js';
import { bindInfoTipDismiss } from './core/facts.js';
import { bindTooltipDelegation } from './core/tooltip.js';
import { loadHome, initHomeLayout } from './views/home.js';
import { bindCoachSetting } from './views/settings.js';
import { renderMeta, loadRangeData } from './shell/loader.js';
import { renderRangeButtons, bindCustomRangePanel } from './shell/range.js';
import { applyRoute, bindRouter } from './shell/router.js';
import { bindSyncButtons } from './shell/sync.js';
import { bindCoachPanel, bindAnalysisButtons, bindResize } from './shell/coach-frame.js';
import { initCoach } from './coach.js';

/**
 * 啟動時還原上次選過的 range：優先取網址 ?range=，其次是 localStorage
 * 記住的選擇，都沒有才用預設 '30d'。網址參數優先於已儲存設定，讓
 * 「複製網址分享」的情境（例如 ?range=custom:2026-01-01:2026-03-01）
 * 能覆蓋使用者自己之前選的區間。這裡只做格式檢查（custom: 的日期格式）
 * 而非完整的 isValidRangeKey()——meta 此時還沒回來，FALLBACK_RANGES
 * 已足夠涵蓋兩種情況（列舉值或 custom: 格式皆可通過格式檢查）。
 */
function restoreInitialRange() {
  var params = new URLSearchParams(window.location.search);
  var fromUrl = params.get('range');
  if (fromUrl && (FALLBACK_RANGES.indexOf(fromUrl) !== -1 || CUSTOM_RANGE_PATTERN.test(fromUrl))) {
    state.range = fromUrl;
    return;
  }
  var settings = Theme.getSettings();
  var fromSettings = settings && settings.range;
  if (fromSettings && (FALLBACK_RANGES.indexOf(fromSettings) !== -1 || CUSTOM_RANGE_PATTERN.test(fromSettings))) {
    state.range = fromSettings;
  }
}

function init() {
  Theme.init();
  restoreInitialRange();
  bindResize();
  bindTooltipDelegation();
  bindInfoTipDismiss();
  bindCustomRangePanel();
  bindCoachPanel();
  bindAnalysisButtons();
  bindCoachSetting();
  bindSyncButtons();
  bindRouter();
  initHomeLayout();

  // 先套路由再發請求：資料回來時 state.view 已經正確，render-on-activate
  // 才知道該立刻畫還是標髒。反過來會讓首屏的圖表全部畫在隱藏容器裡（寬度 0）。
  applyRoute();

  api('/api/meta').then(function (meta) {
    state.meta = meta;
    if (meta && meta.athlete && meta.athlete.id !== undefined && meta.athlete.id !== null) {
      state.athleteId = meta.athlete.id;
    }
    renderMeta();
    renderRangeButtons(); // 依 meta.ranges 動態產生，需等 meta 回來
  }).catch(function (err) {
    var notice = $('notice-bar');
    clear(notice);
    notice.appendChild(h('span', 'state-error-inline', '無法取得基本資訊：' + err.message));
    renderRangeButtons(); // meta 失敗仍要能操作，退回 FALLBACK_RANGES
  });

  loadHome();
  loadRangeData();

  // AI 教練面板：原本是 coach.js 自己在 DOMContentLoaded 註冊、排在 app 之後執行，
  // 改成 module 後由這裡明確呼叫，維持「App 殼先就緒、面板後啟動」的順序。
  initCoach();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
