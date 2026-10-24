/**
 * loader.js — 載入流程：頁首資訊、依 range 抓取各區塊資料、單場明細
 */

import { state } from '../core/state.js';
import { useMock, api, isOfflineSnapshot, offlineSnapshotInfo } from '../core/api.js';
import { $, clear, h, showLoading, showError } from '../core/dom.js';
import { renderSessionPicker, renderSessionDetail, renderSessionList, renderSessionDetailError } from '../views/sessions.js';
import { loadLoadSeries } from '../views/load.js';
import { loadOverview } from '../views/overview.js';
import { renderOrDefer } from './router.js';
import { renderTrendsPage, showTrendsLoading } from '../views/trends.js';

export function renderMeta() {
  var node = $('meta-info');
  clear(node);
  if (!state.meta) return;
  // 學員與區網警語在系統設定頁「學員與連線」（2026-10-24 由側邊列搬過去：手機看不到側邊列）
  var athlete = state.meta.athlete || {};
  var athleteNode = $('settings-athlete');
  if (athleteNode) athleteNode.textContent = athlete.name || '—';
  var offline = offlineSnapshotInfo();
  if (offline) {
    // 離線快照：側邊列與頂列（手機沒有側邊列）都標資料時間，避免把舊資料當成今天
    var label = '資料截至 ' + offline.at;
    node.appendChild(h('span', 'badge badge-offline', label));
    var stamp = $('offline-stamp');
    if (stamp) {
      stamp.textContent = label;
      stamp.hidden = false;
    }
  } else if (useMock) {
    node.appendChild(h('span', 'badge badge-mock', '開發用假資料'));
  }
  // 後端的部署警語（區網、無身分驗證）對離線檔不適用
  var noticeRow = $('settings-notice-row');
  if (noticeRow) {
    noticeRow.hidden = !(state.meta.notice && !offline);
    $('settings-notice').textContent = state.meta.notice || '';
  }
}

/**
 * 載入某個 range 的所有資料，然後重繪。
 * 這是「設定 range → 重抓 → 重繪」的單一路徑，未來加滑動手勢直接呼叫 setRange 即可。
 */
export function loadRangeData() {
  showLoading($('session-picker'), '載入場次');
  showTrendsLoading();
  renderOrDefer('trends', renderTrendsPage); // 換範圍時各圖先顯示載入中

  var sessionsP = api('/api/sessions?range=' + state.range);
  var wellnessP = api('/api/wellness-trend?range=' + state.range);
  var trainingP = api('/api/training-days?range=' + state.range);
  var recoveryP = api('/api/recovery-impact?range=' + state.range);

  // 場次 + 恢復關聯 → 單場分析與趨勢
  Promise.all([sessionsP, recoveryP]).then(function (res) {
    state.sessions = (res[0] && res[0].sessions) ? res[0].sessions : [];
    state.recoveryImpacts = (res[1] && res[1].impacts) ? res[1].impacts : [];
    renderSessionPicker();
    state.sessionsReady = true;
    renderOrDefer('sessions', renderSessionList);
    renderOrDefer('trends', renderTrendsPage);

    // 訓練紀錄（E.3）預設全部收合，不再自動選最近一場；展開哪一場由網址
    // #/sessions/<id> 決定（router.applySessionRouteArg）。換區間時展開中的
    // 場次照樣保留，明細還沒抓過（或抓的是別場）才補抓。
    var id = state.selectedSessionId;
    if (id !== null && !(state.sessionDetail && state.sessionDetail.id === id)) {
      loadSessionDetail(id);
    }
  }).catch(function (err) {
    showError($('session-picker'), err.message);
    showError($('session-list'), err.message);
    showError($('trends-body'), err.message);
  });

  // 身體狀況 + 訓練日
  Promise.all([wellnessP, trainingP]).then(function (res) {
    state.wellness = res[0];
    state.trainingDays = (res[1] && res[1].training_days) ? res[1].training_days : [];
    renderOrDefer('trends', renderTrendsPage);
  }).catch(function (err) {
    showError($('trends-body'), err.message);
  });

  // Garmin 卡片牆（獨立失敗：拿不到只影響這個類別，各卡顯示無資料）
  api('/api/garmin-cards?range=' + state.range).catch(function () { return { cards: {} }; })
    .then(function (data) {
      state.garminCards = data;
      renderOrDefer('trends', renderTrendsPage);
    });

  // 訓練負荷（獨立於場次/身體狀況，走同一個 range）
  loadLoadSeries();
  loadOverview();
}

export function loadSessionDetail(id) {
  // 保留版面骨架，只在各子區塊顯示載入中
  ['session-summary', 'lap-chart', 'records-chart', 'hr-drift', 'hrzone-chart', 'recovery-impact']
    .forEach(function (key) {
      var n = $(key);
      if (n) showLoading(n, '載入中');
    });

  api('/api/sessions/' + id).then(function (detail) {
    // 回來前使用者已改點別場（或收合）：丟掉這份，不蓋掉目前的展開列
    if (state.selectedSessionId !== id) return;
    state.sessionDetail = detail;
    renderOrDefer('sessions', renderSessionDetail);
  }).catch(function (err) {
    // 離線快照沒有這場的明細（超出 --detail-months 等）不是錯誤，改顯示說明
    renderSessionDetailError(id, err.message, isOfflineSnapshot());
  });
}
