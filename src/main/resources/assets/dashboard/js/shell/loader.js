/**
 * loader.js — 載入流程：頁首資訊、依 range 抓取各區塊資料、單場明細
 */

import { state } from '../core/state.js';
import { useMock, api } from '../core/api.js';
import { $, clear, h, showLoading, showError } from '../core/dom.js';
import { renderSessionPicker, renderSessionDetail, renderSessionList } from '../views/sessions.js';
import { loadLoadSeries, renderTrends } from '../views/load.js';
import { renderWellness } from '../views/wellness.js';
import { loadOverview } from '../views/overview.js';
import { renderOrDefer } from './router.js';

export function renderMeta() {
  var node = $('meta-info');
  clear(node);
  if (!state.meta) return;
  var athlete = state.meta.athlete || {};
  if (athlete.name) {
    node.appendChild(h('span', 'meta-athlete', athlete.name));
  }
  var cov = state.meta.metric_coverage || {};
  if (cov.activities && cov.activities.earliest_date) {
    node.appendChild(h('span', 'meta-cov',
      '活動資料 ' + cov.activities.earliest_date + ' ~ ' + (cov.activities.latest_date || '')));
  }
  if (useMock) {
    node.appendChild(h('span', 'badge badge-mock', '開發用假資料'));
  }
  var notice = $('notice-bar');
  clear(notice);
  if (state.meta.notice) {
    notice.appendChild(h('span', null, state.meta.notice));
  }
}

/**
 * 載入某個 range 的所有資料，然後重繪。
 * 這是「設定 range → 重抓 → 重繪」的單一路徑，未來加滑動手勢直接呼叫 setRange 即可。
 */
export function loadRangeData() {
  var sessionSection = $('session-body');
  var trendNode = $('trend-charts');
  var wellnessNode = $('wellness-charts');

  showLoading($('session-picker'), '載入場次');
  showLoading(trendNode, '載入趨勢');
  showLoading(wellnessNode, '載入身體狀況');

  var sessionsP = api('/api/sessions?range=' + state.range);
  var wellnessP = api('/api/wellness-trend?range=' + state.range);
  var trainingP = api('/api/training-days?range=' + state.range);
  var recoveryP = api('/api/recovery-impact?range=' + state.range);

  // 場次 + 恢復關聯 → 單場分析與趨勢
  Promise.all([sessionsP, recoveryP]).then(function (res) {
    state.sessions = (res[0] && res[0].sessions) ? res[0].sessions : [];
    state.recoveryImpacts = (res[1] && res[1].impacts) ? res[1].impacts : [];
    renderSessionPicker();
    renderOrDefer('sessions', renderSessionList);
    renderOrDefer('load', renderTrends);

    // 預設選最近一場（sessions 由新到舊）
    var keep = state.sessions.some(function (s) { return s.id === state.selectedSessionId; });
    if (!keep) {
      state.selectedSessionId = state.sessions.length > 0 ? state.sessions[0].id : null;
    }
    if (state.selectedSessionId !== null) {
      loadSessionDetail(state.selectedSessionId);
    } else {
      clear(sessionSection);
      sessionSection.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有可分析的場次'));
    }
  }).catch(function (err) {
    showError($('session-picker'), err.message);
    showError(trendNode, err.message);
  });

  // 身體狀況 + 訓練日
  Promise.all([wellnessP, trainingP]).then(function (res) {
    state.wellness = res[0];
    state.trainingDays = (res[1] && res[1].training_days) ? res[1].training_days : [];
    renderOrDefer('wellness', renderWellness);
  }).catch(function (err) {
    showError(wellnessNode, err.message);
  });

  // 訓練負荷（獨立於場次/身體狀況，走同一個 range）
  loadLoadSeries();
  loadOverview();
}

export function loadSessionDetail(id) {
  var body = $('session-body');
  // 保留版面骨架，只在各子區塊顯示載入中
  ['session-summary', 'lap-chart', 'records-chart', 'hr-drift', 'hrzone-chart', 'recovery-impact']
    .forEach(function (key) {
      var n = $(key);
      if (n) showLoading(n, '載入中');
    });

  api('/api/sessions/' + id).then(function (detail) {
    state.sessionDetail = detail;
    renderOrDefer('sessions', function () {
      renderSessionDetail();
      renderSessionList(); // 更新選取樣式
    });
  }).catch(function (err) {
    clear(body);
    showError(body, err.message);
  });
}
