/**
 * router.js — Hash 路由（#/view[/arg]）：切換 view、render-on-activate、標髒延後重繪
 */

import { state, VIEWS, DEFAULT_VIEW, APP_NAME } from '../core/state.js';
import { $ } from '../core/dom.js';
import { todayISO } from '../core/format.js';
import { renderSessionPicker, renderRecoveryImpact, renderSessionDetail, renderSessionList } from '../views/sessions.js';
import { renderCalendarBody, loadCalendarBody } from '../views/calendar.js';
import { loadSessionDetail } from './loader.js';
import { renderTrendsPage } from '../views/trends.js';
import { renderToolsPage } from '../views/tools.js';

// hash 路由 `#/view[/arg]`：
//   #/home  #/calendar  #/calendar/2026-09  #/trends
//   #/sessions  #/sessions/1083  #/tools  #/settings
// 每個 view 是 <section class="view" hidden>，切換只動 hidden——不重建 DOM，
// 圖表與既有容器 id 都原地保留。
//
// 為什麼不在切換時無條件重畫：widthOf() 對 display:none 的容器回 0，
// 隱藏 view 內畫出來的 SVG 寬度會是 0。所以資料載入只渲染「目前」view，
// 其餘標髒（state.dirty），切過去時才補畫。

// 已合併的舊 view → 新 view。舊書籤（#/load、#/wellness）照樣打得開，
// applyRoute 會把網址改寫成新的正規形式。
var LEGACY_VIEWS = { load: 'trends', wellness: 'trends', today: 'home', overview: 'home' };


/** 解析 location.hash → {view, arg}。無效或空的 hash 一律回預設 view。 */
export function parseHash() {
  var raw = (window.location.hash || '').replace(/^#/, '');
  if (raw.indexOf('/') !== 0) return { view: DEFAULT_VIEW, arg: null };
  var parts = raw.slice(1).split('/');
  var view = LEGACY_VIEWS[parts[0]] || parts[0];
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
  if (view === 'sessions' && state.dirty.sessions) {
    state.dirty.sessions = false;
    if (state.sessions.length > 0) renderSessionList();
    if (state.sessionDetail) renderSessionDetail();
  } else if (view === 'trends' && state.dirty.trends) {
    state.dirty.trends = false;
    renderTrendsPage();
  } else if (view === 'calendar' && state.dirty.calendar) {
    state.dirty.calendar = false;
    if (!state.calendarMonth) state.calendarMonth = todayISO().slice(0, 7);
    if (!state.calendarData) loadCalendarBody(); else renderCalendarBody();
  } else if (view === 'tools') {
    renderToolsPage(); // 只在第一次進來時建 DOM，之後原地保留輸入值
  }
}

/**
 * 標記某個 view 的圖表需要重畫。若它正好是目前的 view 就立刻畫，
 * 否則只標髒——這是所有「資料回來了要重繪」路徑的統一入口。
 * @param {string} key dirty 的 key，同 view 名稱（sessions|trends|calendar）
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

export function bindRouter() {
  window.addEventListener('hashchange', applyRoute);
  var back = $('session-back');
  if (back) back.addEventListener('click', function () { navigate('sessions'); });
}
