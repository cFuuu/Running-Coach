/**
 * sync.js — 資料同步：最後同步時間的呈現、同步按鈕（dry run → 確認 → 實際同步）
 */

import * as C from '../charts.js';
import { state } from '../core/state.js';
import { api, apiPost } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { shortDateTime } from '../core/format.js';
import { loadHome } from '../views/home.js';
import { loadCalendarBody } from '../views/calendar.js';

// N-19 §7。同步是**長時間操作**（wellness 逐日各打數個端點並自我節流，
// 90 天約 12 分鐘），所以流程刻意分兩步：
//
//   1. dry_run=True 拿 estimate.summary（「45 天／226 次請求／約 3 分 46 秒」）
//   2. 使用者確認後才真的執行
//
// 這是 2026-09-01 與 Fu 議定的行為，CLI 與 MCP 都照做，dashboard 沒有理由
// 自己發明一套。兩顆鈕（右上角工具列與設定頁）都標了 data-action="sync"，
// 狀態一起更新——否則會出現「設定頁顯示同步中、右上角還能再按一次」。

/**
 * 「最後同步」的共用渲染。取 /api/today 的 freshness，只顯示較新的那個
 * 日期與落後天數——側邊列空間窄，完整的兩個日期留在今日卡（見 renderToday
 * 中對 days_behind 的說明）。
 *
 * 側邊列底部（D.2）與設定頁的資料同步區都用這一份，兩處因此不可能顯示
 * 不一致的值；`showLabel` 只差在設定頁已有「最後同步」的欄位名，不必再
 * 重複一次。
 * @param {string} nodeId 容器 id
 * @param {boolean} showLabel 是否附上「最後同步」字樣
 */
/**
 * 最後更新時間（側邊列底部常駐，系統設定頁也用同一份）：
 *   第一行：資料最後一次同步進本機的時刻（後端給 UTC，這裡換成當地時間）
 *   其後：最新一場活動的開始時間、最新一天的身體數據、落後天數
 * 來源是 /api/today 的 freshness——每一頁都會載入它，所以哪一頁都看得到。
 */
export function renderSyncFreshness(nodeId, showLabel) {
  var node = $(nodeId);
  if (!node) return;
  clear(node);
  var f = state.today && state.today.freshness;
  if (!f) return;
  if (!f.last_synced_at && !f.activity_latest && !f.wellness_latest) {
    node.appendChild(h('span', 'sync-label', '尚無同步資料'));
    return;
  }
  if (showLabel) node.appendChild(h('span', 'sync-label', '最後更新時間'));
  node.appendChild(h('span', 'sync-value', shortDateTime(f.last_synced_at)));
  // 以下各行在 72px 圖示欄放不下，由 CSS 隱藏（同步時刻仍看得見）
  node.appendChild(h('span', 'sync-detail', '最新活動 ' + shortDateTime(f.activity_latest_at)));
  node.appendChild(h('span', 'sync-detail',
    '身體數據 ' + (f.wellness_latest ? C.shortDate(f.wellness_latest) : '—')));
  var days = f.days_behind;
  if (days !== null && days !== undefined) {
    node.appendChild(h('span', 'sync-behind', days > 0 ? '資料落後 ' + days + ' 天' : '資料已到今天'));
  }
}

/** 落後天數 → 新鮮度圓點顏色級別：今天＝綠、落後 1 天＝黃、2 天以上＝紅、未知＝灰。 */
function freshnessTier(days) {
  if (days === null || days === undefined) return 'is-unknown';
  if (days <= 0) return 'is-fresh';
  if (days === 1) return 'is-stale';
  return 'is-old';
}

/**
 * 側邊列的資料新鮮度：一行「圓點＋狀態＋同步時刻」，圓點顏色表示新鮮度；
 * 最新活動、身體數據、落後天數收進 hover／聚焦／點擊展開的浮層。
 * 側邊列是 overflow 捲動容器，浮層用 fixed 定位（show 時依按鈕位置計算）才不會被裁掉。
 * 設定頁仍用 renderSyncFreshness 的完整明細，不受影響。
 */
export function renderSidebarSync() {
  var node = $('sidebar-sync');
  if (!node) return;
  clear(node);
  var f = state.today && state.today.freshness;
  if (!f) return;
  if (!f.last_synced_at && !f.activity_latest && !f.wellness_latest) {
    node.appendChild(h('span', 'sync-label', '尚無同步資料'));
    return;
  }
  var days = f.days_behind;
  var status = days === null || days === undefined ? '資料時間'
    : (days > 0 ? '落後 ' + days + ' 天' : '資料已到今天');

  var btn = h('button', 'sync-chip ' + freshnessTier(days));
  btn.type = 'button';
  btn.setAttribute('aria-expanded', 'false');
  btn.appendChild(h('span', 'sync-dot'));
  btn.appendChild(h('span', 'sync-status', status));
  btn.appendChild(h('span', 'sync-value', shortDateTime(f.last_synced_at)));
  node.appendChild(btn);

  var pop = h('div', 'sync-popover');
  pop.setAttribute('role', 'tooltip');
  pop.appendChild(h('div', 'sync-pop-title', '資料更新時間'));
  [
    ['最後同步', shortDateTime(f.last_synced_at)],
    ['最近活動', shortDateTime(f.activity_latest_at)]
  ].forEach(function (r) {
    var row = h('div', 'sync-pop-row');
    row.appendChild(h('span', null, r[0]));
    row.appendChild(h('span', 'sync-pop-val', r[1]));
    pop.appendChild(row);
  });
  node.appendChild(pop);

  function show() {
    var r = btn.getBoundingClientRect();
    pop.style.left = (r.right + 8) + 'px';
    pop.style.bottom = Math.max(8, window.innerHeight - r.bottom) + 'px';
    pop.classList.add('is-open');
    btn.setAttribute('aria-expanded', 'true');
  }
  function hide() {
    pop.classList.remove('is-open');
    btn.setAttribute('aria-expanded', 'false');
  }
  btn.addEventListener('mouseenter', show);
  btn.addEventListener('mouseleave', hide);
  btn.addEventListener('focus', show);
  btn.addEventListener('blur', hide);
  btn.addEventListener('click', function () {
    if (pop.classList.contains('is-open')) hide(); else show();
  });
}

export var syncState = { running: false };

export function syncButtons() {
  return document.querySelectorAll('[data-action="sync"]');
}

export function setSyncBusy(busy, label) {
  syncState.running = busy;
  Array.prototype.forEach.call(syncButtons(), function (btn) {
    btn.disabled = busy;
    btn.classList.toggle('is-syncing', busy);
    // 右上角是純 icon 鈕（無文字節點），只有設定頁那顆要換字
    var textNode = btn.querySelector('.setting-btn-text');
    if (textNode && label) textNode.textContent = label;
  });
}

export function setSyncMessage(text, kind) {
  var node = $('settings-sync-message');
  if (!node) return;
  clear(node);
  if (!text) return;
  node.className = 'setting-sync-message' + (kind ? ' is-' + kind : '');
  node.appendChild(document.createTextNode(text));
}

/**
 * 同步失敗時的結果不是例外，而是 available=False ＋ reason（見
 * routes_sync 說明：那不是「請求錯了」而是「這次做不成」）。
 * rate_limited 要特別提醒不可重試——重試會把帳號推得更靠近被鎖。
 */
export function describeSyncFailure(result) {
  var reason = result.reason || '同步失敗，原因不明';
  if (result.rate_limited) {
    return '已觸發 Garmin 的請求限制，請稍後再試（不要連續重試）。' + reason;
  }
  return reason;
}

export function requestSync() {
  if (syncState.running) return;
  setSyncBusy(true, '估算中…');
  setSyncMessage('正在估算同步範圍…', null);

  apiPost('/api/sync', { dry_run: true }).then(function (plan) {
    if (!plan.available) {
      setSyncBusy(false, '同步資料');
      setSyncMessage(describeSyncFailure(plan), 'error');
      return;
    }

    var summary = (plan.estimate && plan.estimate.summary) || (plan.days + ' 天');
    var lines = ['即將同步：' + summary];
    if (plan.start_date && plan.end_date) {
      lines.push('區間：' + plan.start_date + ' ~ ' + plan.end_date);
    }
    if (plan.gap_truncated) {
      lines.push('⚠️ 距上次同步已超過 90 天，更早的缺口本次補不到，'
        + '需改下載官方匯出包並在終端機執行 garmin_import_runner。');
    }
    lines.push('要開始同步嗎？');

    if (!window.confirm(lines.join('\n'))) {
      setSyncBusy(false, '同步資料');
      setSyncMessage('已取消', null);
      return;
    }

    setSyncBusy(true, '同步中…');
    setSyncMessage('同步中，預計 ' + summary + '。請勿關閉此頁。', null);

    return apiPost('/api/sync', {}).then(function (result) {
      setSyncBusy(false, '同步資料');
      if (!result.available) {
        setSyncMessage(describeSyncFailure(result), 'error');
        return;
      }
      var parts = [];
      if (result.activities !== undefined) parts.push('活動 ' + result.activities + ' 筆');
      if (result.wellness !== undefined) parts.push('身體數據 ' + result.wellness + ' 筆');
      setSyncMessage('同步完成：' + (parts.join('　·　') || '無新資料'), 'ok');
      // 同步會改變資料，首頁與行事曆必須重讀，否則畫面還停在同步前
      loadHome();
      if (state.calendarData) loadCalendarBody();
    });
  }).catch(function (err) {
    setSyncBusy(false, '同步資料');
    setSyncMessage(err.message, 'error');
  });
}

/**
 * 同步鈕（右上角工具列與設定頁各一顆，都標了 data-action="sync"）。
 * 兩顆的 disabled 由 /api/sync/status 決定：來源不被允許時（例如從區網
 * 裝置連進來）維持 disabled 並顯示理由，而不是讓使用者按下去才吃 403。
 */
export function bindSyncButtons() {
  Array.prototype.forEach.call(syncButtons(), function (btn) {
    btn.addEventListener('click', requestSync);
  });

  api('/api/sync/status').then(function (status) {
    if (status.client_allowed) {
      setSyncBusy(false, '同步資料');
    } else {
      Array.prototype.forEach.call(syncButtons(), function (btn) {
        btn.disabled = true;
        btn.title = status.reason || '此裝置不允許執行同步';
      });
      setSyncMessage(status.reason, 'error');
    }
  }).catch(function () {
    // status 拿不到就維持 HTML 的 disabled 預設值，不猜
  });
}
