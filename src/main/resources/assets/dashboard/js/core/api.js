/**
 * api.js — 資料存取層：GET／POST 包裝、athlete_id 附加、?mock=1 假資料切換
 */

import { state } from './state.js';

export var useMock = (function () {
  var params = new URLSearchParams(window.location.search);
  var flag = params.get('mock');
  if (flag === '0') return false;              // 強制走真實 API
  if (flag === '1') return !!window.MOCK_API;  // 強制走假資料
  return !!window.MOCK_API;                    // 預設：真實 API（mock-data.js 不再預設載入）
})();

/**
 * 附加 athlete_id。尚未從 /api/meta 取得時不附加（後端預設取第一位學員）。
 * 所有請求（含 coach.js 的聊天與分析）都經過這裡，呼叫端不可自己再接一次。
 */
export function withAthlete(path) {
  var id = state.athleteId;
  if (id === null || id === undefined) return path;
  return path + (path.indexOf('?') === -1 ? '?' : '&') + 'athlete_id=' + encodeURIComponent(id);
}

/**
 * 統一的 API 請求入口（相對路徑，不寫死主機位址）。
 * @param {string} path 例如 '/api/sessions?range=30d'
 * @returns {Promise<Object>}
 */
export function api(path) {
  var full = withAthlete(path);
  if (useMock) return window.MOCK_API.request(full);

  return fetch('.' + full, { headers: { Accept: 'application/json' } })
    .then(function (res) {
      if (!res.ok) throw new Error('API 回應 ' + res.status + '（' + path + '）');
      return res.json();
    });
}

/**
 * POST 版的 api()。目前只有 /api/sync 用得到。
 *
 * 與 api() 分開而非加參數，是因為兩者的錯誤語意不同：GET 失敗多半是
 * 資料還沒進來，POST 失敗（403／422）要把後端的 detail 原文顯示給使用者
 * ——那是可操作的指引（例如「請設定 RC_SYNC_ALLOW_PRIVATE_NETWORK=1」），
 * 吞掉只留狀態碼會讓使用者不知道下一步該做什麼。
 */
export function apiPost(path, body) {
  var full = withAthlete(path);
  if (useMock) return window.MOCK_API.request(full, body);

  return fetch('.' + full, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  }).then(function (res) {
    return res.json().catch(function () { return {}; }).then(function (data) {
      if (!res.ok) {
        throw new Error(data.detail || ('API 回應 ' + res.status + '（' + path + '）'));
      }
      return data;
    });
  });
}
