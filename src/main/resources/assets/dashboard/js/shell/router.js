/**
 * router.js — Hash 路由（#/view[/arg]）：切換 view、render-on-activate、標髒延後重繪
 */

import { state, VIEWS, DEFAULT_VIEW, APP_NAME } from '../core/state.js';
import { $ } from '../core/dom.js';
import { todayISO } from '../core/format.js';
import { renderSessionPicker, renderRecoveryImpact, renderSessionDetail, renderSessionList } from '../views/sessions.js';
import { renderCalendarBody, loadCalendarBody } from '../views/calendar.js';
import { renderLoad, renderTrends } from '../views/load.js';
import { renderWellness } from '../views/wellness.js';
import { renderOverview } from '../views/overview.js';
import { loadSessionDetail } from './loader.js';

/** 解析 location.hash → {view, arg}。無效或空的 hash 一律回預設 view。 */
export function parseHash() {
  var raw = (window.location.hash || '').replace(/^#/, '');
  if (raw.indexOf('/') !== 0) return { view: DEFAULT_VIEW, arg: null };
  var parts = raw.slice(1).split('/');
  var view = parts[0];
  if (!VIEWS[view]) return { view: DEFAULT_VIEW, arg: null };
  return { view: view, arg: parts.length > 1 && parts[1] !== '' ? parts[1] : null };
}

/**
 * 切到指定 view（寫 hash，實際切換由 hashchange 統一處理，
 * 確保「點導覽」與「直接改網址／上一頁」走同一條路徑）。
 * @param {string} view VIEWS 的 key
 * @param {string|number} [arg] sessions 用 session id、calendar 用 'YYYY-MM'
 */
export function navigate(view, arg) {
  if (!VIEWS[view]) return;
  var hash = '#/' + view + (arg !== null && arg !== undefined && arg !== '' ? '/' + arg : '');
  if (window.location.hash === hash) {
    applyRoute(); // hash 沒變不會觸發 hashchange，直接套用（例如重選同一場）
    return;
  }
  window.location.hash = hash;
}

/** 讀目前 hash 並套用到畫面：顯示對應 view、更新導覽高亮、處理參數。 */
export function applyRoute() {
  var route = parseHash();
  var view = route.view;

  // 網址沒有（或帶著無效的）hash 時補上正規形式，讓網址永遠反映目前
  // 位置——否則首頁複製出去的連結會失去 view，重新整理也回不到原處。
  // 用 replaceState 不留下多餘的上一頁紀錄；它不觸發 hashchange，
  // 所以下面照常繼續套用。
  var canonical = '#/' + view + (route.arg ? '/' + route.arg : '');
  if (window.location.hash !== canonical && window.history && window.history.replaceState) {
    var url = new URL(window.location.href);
    url.hash = canonical;
    window.history.replaceState(null, '', url.toString());
  }

  state.view = view;

  // 只動 hidden，不重建 DOM
  Object.keys(VIEWS).forEach(function (key) {
    var section = $('view-' + key);
    if (section) section.hidden = (key !== view);
  });

  // 導覽高亮（側邊列、底部分頁列、右上角齒輪共用 data-view）。
  // 齒輪也列入，是因為系統設定已從側邊列移除，它是該 view 的唯一入口。
  var links = document.querySelectorAll('.nav-link, .tab-link, .tool-btn[data-view]');
  Array.prototype.forEach.call(links, function (link) {
    var active = link.getAttribute('data-view') === view;
    link.classList.toggle('is-active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  // 頂列：標題與 range 切換（只有吃 range 的 view 顯示區間切換）
  var titleEl = $('content-title');
  if (titleEl) titleEl.textContent = VIEWS[view].title;
  var controls = $('topbar-controls');
  if (controls) controls.hidden = !VIEWS[view].hasRange;
  document.title = VIEWS[view].title + '｜' + APP_NAME;

  // 路由參數
  if (view === 'sessions') {
    applySessionRouteArg(route.arg);
  } else if (view === 'calendar' && route.arg && /^\d{4}-\d{2}$/.test(route.arg)) {
    if (state.calendarMonth !== route.arg) {
      state.calendarMonth = route.arg;
      state.calendarData = null;
      state.dirty.calendar = true;
    }
  }

  // 切 view 時內容區捲回頂端（各 view 是獨立畫面，不該沿用上一個的捲動位置）
  var content = $('content');
  if (content && content.scrollTo) content.scrollTo(0, 0);

  // 行事曆是唯一「進來才載入」的 view（月曆資料量大且與 range 無關），
  // 首次進入或換月時都由這裡觸發。
  if (view === 'calendar' && !state.calendarData) state.dirty.calendar = true;

  renderDirtyView(view);
  updateSessionsMobileStage();
}

/**
 * `#/sessions/<id>` 的參數處理。深連結（重新整理後直接開這個網址）時
 * state.sessions 可能還沒回來，此時只先記住 id，等 loadRangeData 完成
 * 後的既有「保留已選場次」邏輯會接手載入明細。
 */
export function applySessionRouteArg(arg) {
  if (arg === null) return;
  var id = parseInt(arg, 10);
  if (isNaN(id)) return;
  if (state.selectedSessionId === id && state.sessionDetail) return;
  state.selectedSessionId = id;
  if (state.sessions.length > 0) {
    renderSessionPicker();
    renderSessionList();
    renderRecoveryImpact();
  }
  loadSessionDetail(id);
}

/** 切到某 view 時，若它的圖表在隱藏期間被標髒就補畫。 */
export function renderDirtyView(view) {
  if (view === 'overview' && state.dirty.overview) {
    state.dirty.overview = false;
    renderOverview();
  } else if (view === 'sessions' && state.dirty.sessions) {
    state.dirty.sessions = false;
    if (state.sessions.length > 0) renderSessionList();
    if (state.sessionDetail) renderSessionDetail();
  } else if (view === 'load' && state.dirty.load) {
    state.dirty.load = false;
    if (state.loadSeries) renderLoad();
    if (state.sessions.length > 0) renderTrends();
  } else if (view === 'wellness' && state.dirty.wellness) {
    state.dirty.wellness = false;
    if (state.wellness) renderWellness();
  } else if (view === 'calendar' && state.dirty.calendar) {
    state.dirty.calendar = false;
    if (!state.calendarMonth) state.calendarMonth = todayISO().slice(0, 7);
    if (!state.calendarData) loadCalendarBody(); else renderCalendarBody();
  }
}

/**
 * 標記某個 view 的圖表需要重畫。若它正好是目前的 view 就立刻畫，
 * 否則只標髒——這是所有「資料回來了要重繪」路徑的統一入口。
 * @param {string} key dirty 的 key（sessions|load|wellness|calendar）
 * @param {Function} renderFn 真正的渲染函式
 */
export function renderOrDefer(key, renderFn) {
  if (state.view === key) renderFn();
  else state.dirty[key] = true;
}

/**
 * 手機的訓練紀錄「清單 → 明細」兩段式：有選定場次時顯示明細、否則
 * 顯示清單。桌機兩欄並存，這個 class 在 CSS 的手機斷點才生效。
 */
export function updateSessionsMobileStage() {
  var view = $('view-sessions');
  if (!view) return;
  var route = parseHash();
  var showDetail = state.view === 'sessions' && route.arg !== null;
  var wasDetail = view.classList.contains('stage-detail');
  view.classList.toggle('stage-detail', showDetail);
  view.classList.toggle('stage-list', !showDetail);

  // 手機清單階段時明細是 display:none，其中的圖表寬度為 0。切到明細
  // 階段等於「容器從隱藏變可見」，和切 view 是同一種情況，必須重畫。
  if (showDetail && !wasDetail && state.sessionDetail && window.innerWidth < 768) {
    renderSessionDetail();
  }
}

//
// N-19 §7。同步是**長時間操作**（wellness 逐日各打數個端點並自我節流，
// 90 天約 7.5 分鐘），所以流程刻意分兩步：
//
//   1. dry_run=True 拿 estimate.summary（「45 天／226 次請求／約 3 分 46 秒」）
//   2. 使用者確認後才真的執行
//
// 這是 2026-09-01 與 Fu 議定的行為，CLI 與 MCP 都照做，dashboard 沒有理由
// 自己發明一套。兩顆鈕（右上角工具列與設定頁）都標了 data-action="sync"，
// 狀態一起更新——否則會出現「設定頁顯示同步中、右上角還能再按一次」。

export function bindRouter() {
  window.addEventListener('hashchange', applyRoute);
  var back = $('session-back');
  if (back) back.addEventListener('click', function () { navigate('sessions'); });
}

//
// S1 只負責「殼」：三種寬度的形態（桌機常駐欄／平板抽屜／手機全螢幕 sheet）
// 與收合狀態的存讀。對話、串流、工具 chip 屬 S2／S3 的 coach.js，這裡不碰。
// 收合偏好沿用既有的 Theme.saveSettings，不另開 localStorage key。
