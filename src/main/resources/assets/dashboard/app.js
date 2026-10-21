/**
 * app.js — 狀態管理與資料抓取
 *
 * 【架構契約】
 *   本檔負責：range 狀態、選定場次狀態、fetch、錯誤／載入處理、呼叫 charts.js 重繪。
 *   charts.js 只負責畫圖（純函式），兩者不可互相污染。
 *
 *   將來要加「滑動切換場次／滑動改變 range」的手勢互動時，
 *   只需在此檔呼叫既有的 setRange() / selectSession()，
 *   它們已經是「設定狀態 → 重抓 → 重繪」的單一路徑，charts.js 不用改。
 *
 * 【API base URL】
 *   一律用相對路徑（後端會把靜態檔掛在 /，API 在 /api/*），不寫死絕對網址。
 *
 * 【假資料】
 *   預設走真實 API。mock-data.js 已於 2026-09-08 停止預設載入，僅在網址帶
 *   ?mock=1 時由 index.html 動態插入（見該檔結尾），此時所有請求改走假資料。
 */
(function () {
  'use strict';

  var C = window.Charts;

  // ------------------------------------------------------------ 狀態

  var state = {
    range: '30d',
    athleteId: null,       // 未指定時由後端取資料庫第一位
    sessions: [],
    selectedSessionId: null,
    sessionDetail: null,
    wellness: null,
    trainingDays: [],
    recoveryImpacts: [],
    meta: null,
    today: null,
    loadSeries: null,
    calendarMonth: null,   // 'YYYY-MM'，展開月曆時才需要，預設當月
    calendarData: null,    // /api/calendar 回傳的月曆資料（展開後才載入）
    calendarSelectedDate: null, // 'YYYY-MM-DD'，月曆下方明細顯示的是哪一天
    weekDays: null,        // /api/calendar 的本週資料（週條用）

    // 今日頁的 KPI 條與最近訓練清單。兩者都取固定區間（近 30 天），
    // 與頂列的 range 切換無關——今日頁不吃 range（見 VIEWS.today.hasRange）。
    todayKpi: null,        // { loadSeries, hrvPoints, rhrPoints }
    recentSessions: null,  // /api/sessions 近 30 天的場次（取前 8 筆顯示）
    overview: null,        // /api/overview（吃 range）；ATL/CTL/TSB 另用 loadSeries
    overviewWeek: null,    // 總覽「本週課表」：/api/calendar 的本 ISO 週（週一起算）

    view: 'overview',      // 目前 view：today|overview|calendar|sessions|load|wellness

    // render-on-activate 的髒標記。widthOf() 對 display:none 的容器回 0，
    // 在隱藏的 view 裡畫圖會得到寬度 0 的空 SVG，所以資料回來時若該 view
    // 不在畫面上就只標髒、不畫；切過去時才補畫（見 renderDirtyView）。
    dirty: { overview: false, sessions: false, load: false, wellness: false, calendar: false }
  };

  // view 的定義：路由 key → 標題、是否吃 range。
  // 「吃 range」決定內容頂列的區間切換要不要顯示（D.2：只有訓練紀錄／
  // 訓練負荷／身體狀況 三個 view 顯示，今日、行事曆與系統設定不受區間影響）。
  var VIEWS = {
    today:    { title: '今日',         hasRange: false },
    overview: { title: '總覽',         hasRange: true },
    calendar: { title: '行事曆',       hasRange: false },
    sessions: { title: '訓練紀錄',     hasRange: true },
    load:     { title: '訓練負荷',     hasRange: true },
    wellness: { title: '身體狀況',     hasRange: true },
    settings: { title: '系統設定',     hasRange: false }
  };
  var DEFAULT_VIEW = 'overview';

  // 產品名。分頁標題組成 `<view 標題>｜Running Coach`，<title> 的初始值
  // （index.html）是完整的「Running Coach 訓練儀表板」——前者要短，因為
  // 瀏覽器分頁很窄，view 標題才是使用者在一排分頁裡真正要分辨的東西。
  var APP_NAME = 'Running Coach';

  // range 的合法值改由 /api/meta 的 ranges 動態提供（後端 RANGE_LABELS
  // 是唯一真實來源），這裡只留一個防禦性 fallback：若 meta 還沒回來前
  // 使用者就想操作（理論上不會發生，因為按鈕本身也是 meta 回來後才產生），
  // 或 meta 請求失敗時，仍能辨識這 5 個預設值與 custom: 前綴格式。
  var FALLBACK_RANGES = ['7d', '30d', '90d', '1y', 'all'];
  var CUSTOM_RANGE_PREFIX = 'custom:';
  var CUSTOM_RANGE_PATTERN = /^custom:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/;

  function isValidRangeKey(range) {
    var known = (state.meta && state.meta.ranges)
      ? state.meta.ranges.map(function (r) { return r.key; })
      : FALLBACK_RANGES;
    if (known.indexOf(range) !== -1) return true;
    return CUSTOM_RANGE_PATTERN.test(range);
  }

  // 身體狀況指標定義改由後端 /api/meta 的 wellness_metric_defs 驅動
  // （後端 WELLNESS_METRIC_DEFS 是唯一真實來源：label/unit/decimals/format/
  // default_hidden），不再於前端寫死一份。見 wellnessMetricDefs() 與
  // FALLBACK_WELLNESS_METRICS（meta 請求失敗時的最小降級路徑）。
  var FALLBACK_WELLNESS_METRICS = [
    { key: 'hrv_ms', label: 'HRV 夜間均值', unit: 'ms', decimals: 1, format: 'number', default_hidden: false },
    { key: 'resting_hr_bpm', label: '靜止心率', unit: 'bpm', decimals: 0, format: 'number', default_hidden: false },
    { key: 'spo2_pct', label: '血氧', unit: '%', decimals: 0, format: 'number', default_hidden: false },
    { key: 'sleep_score', label: '睡眠分數', unit: '', decimals: 0, format: 'number', default_hidden: false },
    { key: 'training_readiness_score', label: '訓練準備度', unit: '', decimals: 0, format: 'number', default_hidden: false },
    { key: 'stress_avg', label: '壓力（睡眠期間）', unit: '', decimals: 0, format: 'number', default_hidden: false },
    { key: 'all_day_stress_avg', label: '壓力（全天）', unit: '', decimals: 0, format: 'number', default_hidden: false },
    { key: 'steps', label: '步數', unit: '步', decimals: 0, format: 'number', default_hidden: false }
  ];

  function wellnessMetricDefs() {
    return (state.meta && state.meta.wellness_metric_defs) || FALLBACK_WELLNESS_METRICS;
  }

  /**
   * 依使用者存的 order/hidden 排出最終要渲染的指標定義清單。
   * 關鍵設計：存的是「順序」與「隱藏清單」，不是「完整可見清單」——
   * 後端新增指標時，不在使用者 order 裡的 key 會排到末端自動出現，
   * 而不是被舊的 localStorage 永久隱藏（這是容易踩到的坑：使用者會
   * 以為新指標沒生效）。已不存在於目前 defs 的舊 key 直接忽略。
   */
  function orderedVisibleWellnessDefs() {
    var defs = wellnessMetricDefs();
    var byKey = {};
    defs.forEach(function (d) { byKey[d.key] = d; });

    var settings = window.Theme && window.Theme.getSettings ? window.Theme.getSettings() : {};
    var wellnessSettings = settings.wellness || {};
    var order = Array.isArray(wellnessSettings.order) ? wellnessSettings.order : [];
    var hidden = Array.isArray(wellnessSettings.hidden) ? wellnessSettings.hidden : null;
    // hidden 為 null（使用者從未動過設定）時，用後端 default_hidden 決定初始顯隱；
    // 使用者一旦動過設定，hidden 陣列就是完整的隱藏清單（含或不含 default_hidden 的項目）。

    var ordered = [];
    order.forEach(function (key) {
      if (byKey[key]) { ordered.push(byKey[key]); delete byKey[key]; }
    });
    defs.forEach(function (d) { if (byKey[d.key]) ordered.push(d); }); // 未知/新指標排末端

    return ordered.filter(function (d) {
      return hidden ? hidden.indexOf(d.key) === -1 : !d.default_hidden;
    });
  }

  function saveWellnessSettings(order, hidden) {
    if (window.Theme && window.Theme.saveSettings) {
      window.Theme.saveSettings({ wellness: { order: order, hidden: hidden } });
    }
  }

  var WORKOUT_TYPE_LABEL = {
    easy: '輕鬆跑',
    tempo: '節奏跑',
    interval: '間歇',
    lsd: '長距離慢跑',
    race: 'Race',
    recovery: '恢復跑',
    unknown: '未分類',
    rest: '休息',
    strength: '重訓課',
    cross_training: '交叉訓練'
  };

  var SOURCE_LABEL = { auto: '自動判定', manual: '手動設定' };

  // 今日卡／行事曆用：activity_type → 顯示名稱與 CSS 色票 key
  // （--activity-* 變數，見 styles.css；未列出的類型退回 activity-other）。
  var ACTIVITY_TYPE_LABEL = {
    running: '跑步',
    treadmill_running: '跑步機',
    track_running: '田徑場跑步',
    trail_running: '越野跑',
    strength_training: '重訓',
    walking: '健走',
    cycling: '騎車',
    hiking: '登山'
  };
  var ACTIVITY_COLOR_VARS = {
    running: '--activity-running',
    treadmill_running: '--activity-running',
    track_running: '--activity-running',
    trail_running: '--activity-running',
    strength_training: '--activity-strength_training',
    walking: '--activity-walking',
    cycling: '--activity-cycling'
  };
  function activityColorVar(activityType) {
    return ACTIVITY_COLOR_VARS[activityType] || '--activity-other';
  }

  var DAY_STATE_LABEL = {
    rest_ok: '休息',
    planned_done: '完成',
    planned_missed: '未執行',
    today_pending: '今天待完成',
    unplanned_activity: '額外訓練',
    future: '尚未發生'
  };

  var CONFIDENCE_LABEL = { high: '可信', low: '低信賴', unusable: '不可用' };

  // Garmin（Firstbeat）的 HRV 狀態分類。值來自 daily_wellness.hrv_status，
  // 由手錶端演算法判定，本專案只做顯示與標注偏誤，不重新計算也不覆寫。
  var HRV_STATUS_LABEL = {
    BALANCED: '平衡',
    UNBALANCED: '不平衡',
    LOW: '偏低',
    POOR: '不佳',
    NOT_ENOUGH_DATA: '資料不足'
  };
  // 只有「平衡」是預期狀態，其餘都值得注意——用 CSS class 區分顏色，
  // 但不在這裡寫任何建議文字（判讀屬 AI 教練，見頁尾聲明）。
  var HRV_STATUS_CLASS = {
    BALANCED: 'hrv-status-balanced',
    UNBALANCED: 'hrv-status-alert',
    LOW: 'hrv-status-alert',
    POOR: 'hrv-status-alert',
    NOT_ENOUGH_DATA: 'hrv-status-muted'
  };

  /**
   * 把 data_confidence.fact() 的三欄位輸出（{value, source, confidence}）
   * 渲染成一個帶信賴度 badge 的行內元素。fact 為 null／undefined 時顯示
   * 「未評」（§7.3 硬規則：無法標記信賴度就不輸出裸數字，前端對應地
   * 不可把 null 顯示成 0 或空白，必須明確標示未評）。
   * @param {Object|null} fact {value, source, confidence:{level, biases}}
   * @param {function} formatter 把 value 轉成顯示字串
   * @param {string} [unit]
   */
  function factValueNode(fact, formatter, unit) {
    var wrap = h('span', 'today-fact-value');
    if (!fact || fact.value === null || fact.value === undefined) {
      wrap.appendChild(h('span', null, '未評'));
      var noneBadge = h('span', 'conf-badge conf-none', '無資料');
      wrap.appendChild(noneBadge);
      return wrap;
    }
    wrap.appendChild(document.createTextNode(formatter(fact.value) + (unit || '')));
    wrap.appendChild(confBadge(fact));
    return wrap;
  }

  // ------------------------------------------------------------ 資料存取層

  var useMock = (function () {
    var params = new URLSearchParams(window.location.search);
    var flag = params.get('mock');
    if (flag === '0') return false;              // 強制走真實 API
    if (flag === '1') return !!window.MOCK_API;  // 強制走假資料
    return !!window.MOCK_API;                    // 預設：真實 API（mock-data.js 不再預設載入）
  })();

  /**
   * 統一的 API 請求入口（相對路徑，不寫死主機位址）。
   * @param {string} path 例如 '/api/sessions?range=30d'
   * @returns {Promise<Object>}
   */
  function api(path) {
    var full = path;
    if (state.athleteId !== null && state.athleteId !== undefined) {
      full += (path.indexOf('?') === -1 ? '?' : '&') + 'athlete_id=' + encodeURIComponent(state.athleteId);
    }
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
  function apiPost(path, body) {
    var full = path;
    if (state.athleteId !== null && state.athleteId !== undefined) {
      full += (path.indexOf('?') === -1 ? '?' : '&') + 'athlete_id=' + encodeURIComponent(state.athleteId);
    }
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

  // ------------------------------------------------------------ DOM 小工具

  function $(id) { return document.getElementById(id); }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /** 建立元素 */
  function h(tag, className, textContent) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (textContent !== undefined && textContent !== null) node.textContent = textContent;
    return node;
  }

  /** 在容器內顯示「載入中」 */
  function showLoading(node, label) {
    clear(node);
    node.appendChild(h('div', 'state-msg state-loading', (label || '載入中') + '…'));
  }

  /** 在容器內顯示錯誤 */
  function showError(node, message) {
    clear(node);
    var box = h('div', 'state-msg state-error');
    box.appendChild(h('strong', null, '讀取失敗'));
    box.appendChild(h('span', null, message));
    node.appendChild(box);
  }

  /**
   * 顯示 available:false 的說明（直接呈現 API 回傳的 reason，不畫空圖）。
   */
  function showUnavailable(node, reason) {
    clear(node);
    node.appendChild(h('div', 'state-msg state-unavailable', reason || '此項目無資料'));
  }

  /** 建立一張數字卡 */
  function statCard(label, value, sub) {
    var card = h('div', 'stat-card');
    card.appendChild(h('div', 'stat-label', label));
    card.appendChild(h('div', 'stat-value', value));
    if (sub) card.appendChild(h('div', 'stat-sub', sub));
    return card;
  }

  /**
   * 取容器實際像素寬（供圖表決定 X 軸刻度數量）。
   */
  function widthOf(node) {
    var w = node && node.clientWidth ? node.clientWidth : 0;
    return w > 0 ? w : (window.innerWidth || 360);
  }

  /** 把 SVG 放進一個可自行橫向捲動的包裝（整頁 body 不會橫向捲動） */
  function wrapChart(svg) {
    var wrap = h('div', 'chart-wrap');
    wrap.appendChild(svg);
    return wrap;
  }

  // ------------------------------------------------------------ Tooltip／跨圖同步參考線
  //
  // 6 張圖共用同一套命中/顯示邏輯，統一在此處理，不在 charts.js 逐圖各寫一份
  // （charts.js 的架構契約禁止該檔碰 addEventListener／document，見該檔檔頭）。
  //
  // 互動模式分兩種：
  //   離散模式（心率區間圖）：沒有連續時間軸（X 軸是 Z1~Z5 分類），維持單圖
  //   浮動 tooltip，不參與同步游標。
  //
  //   連續模式（其餘 5 張）：仿 Garmin 的「跨圖同步垂直參考線」——滑鼠在
  //   同一個同步群組內任一圖上移動時，同一群組的每張圖都會在對應的邏輯 X
  //   位置畫一條垂直線＋圓點，並在圖表右上角顯示該點數值。三個群組各自獨立
  //   （X 軸基準不同，見 SYNC_GROUPS 常數），不跨群組同步。
  //
  // 綁事件的方式是「事件委派到 document.body，只綁一次」而非替每個 SVG 各綁
  // 一次：app.js 在 resize 後會整批 clear() + 重新產生 SVG（見 bindResize），
  // 若事件綁在個別 SVG 上，重繪後監聽器會全部消失。委派到穩定的 body 則不受
  // 重繪影響，重繪再多次也不用重綁。
  //
  // 座標轉換：charts.js 的 SVG 內部座標系是固定 viewBox（720 寬，見該檔
  // VB_WIDTH），與螢幕實際像素無關，不能直接用 event.offsetX。改用
  // getScreenCTM().inverse() 把螢幕座標換算回 viewBox 座標，這是全站唯一
  // 一處做這個轉換的地方。

  // 同步群組：容器 id → 該群組內所有連續模式圖表共用同一條邏輯 X 軸。
  // 三組互不同步，因為 X 軸基準不同（單場分析＝經過秒數；訓練負荷／
  // 跨場趨勢／身體狀況＝日期字串，兩者字典序恰好與時間序一致，可直接
  // 比較）。用 panel-load 整個面板（而非 trend-charts 子容器）當同步
  // 邊界，讓新增的 TSB 折線（load-chart）與週跑量/距離/配速等跨場趨勢圖
  // 共用同一條垂直參考線——兩者 X 軸基準相同，理當同步。
  var SYNC_GROUPS = ['session-body', 'panel-load', 'wellness-charts'];

  var tooltipEl = null;

  function ensureTooltipEl() {
    if (tooltipEl) return tooltipEl;
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'chart-tooltip';
    tooltipEl.setAttribute('role', 'status');
    tooltipEl.setAttribute('aria-hidden', 'true');
    document.body.appendChild(tooltipEl);
    return tooltipEl;
  }

  function hideTooltip() {
    if (tooltipEl) tooltipEl.classList.remove('is-visible');
  }

  /** meta.tips 的元素可能是純字串，也可能是 {label, value} 結構
   * （見 charts.js 的 trendLineChart／wellnessChart）；純文字 tooltip
   * 場景不需要粗體分段，統一拼成一行字串即可。 */
  function tipToText(tip) {
    if (tip && typeof tip === 'object') return tip.label + ' ' + tip.value;
    return tip;
  }

  /** 把螢幕座標（clientX/clientY）換算成該 SVG 內部 viewBox 座標。 */
  function toSvgPoint(svg, clientX, clientY) {
    var ctm = svg.getScreenCTM();
    if (!ctm) return null;
    var pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    return pt.matrixTransform(ctm.inverse());
  }

  /** 在已排序的陣列中二分搜尋離 target 最近的索引（數字或字典序可比較的字串皆可）。 */
  function nearestIndex(xs, target) {
    var lo = 0;
    var hi = xs.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (xs[mid] < target) lo = mid + 1; else hi = mid;
    }
    if (lo > 0) {
      var distPrev = xs[lo - 1] < target ? target - xs[lo - 1] : xs[lo - 1] - target;
      var distCur = xs[lo] < target ? target - xs[lo] : xs[lo] - target;
      if (distPrev <= distCur) return lo - 1;
    }
    return lo;
  }

  /** 依 clientX/clientY 定位 tooltip，靠近視窗邊緣時翻轉方向避免溢出。 */
  function positionTooltip(clientX, clientY) {
    var el = ensureTooltipEl();
    var margin = 14;
    var rect = el.getBoundingClientRect();
    var left = clientX + margin;
    var top = clientY + margin;
    if (left + rect.width > window.innerWidth) left = clientX - rect.width - margin;
    if (top + rect.height > window.innerHeight) top = clientY - rect.height - margin;
    el.style.left = Math.max(4, left) + 'px';
    el.style.top = Math.max(4, top) + 'px';
  }

  function showTooltipText(text, clientX, clientY) {
    var el = ensureTooltipEl();
    el.textContent = text;
    el.classList.add('is-visible');
    positionTooltip(clientX, clientY);
  }

  /**
   * 找出目前指標所在的 SVG 屬於哪個同步群組容器（回傳容器 DOM 節點），
   * 找不到就回傳 null（例如在群組外的圖，或還沒接上任何群組容器）。
   */
  function findSyncGroupContainer(svg) {
    for (var i = 0; i < SYNC_GROUPS.length; i += 1) {
      var container = $(SYNC_GROUPS[i]);
      if (container && container.contains(svg)) return container;
    }
    return null;
  }

  // 每個同步群組各自一層覆蓋用的游標圖層（垂直線＋圓點＋數值標籤），
  // 掛在該圖各自的 svg 內，clear 時一併清掉不會殘留。
  function clearSyncCursor(svg) {
    var layer = svg.querySelector('.sync-cursor-layer');
    if (layer) layer.parentNode.removeChild(layer);
  }

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function svgEl(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, String(attrs[k]));
    });
    return node;
  }

  // 數值標籤的粗估字元寬度／高度（viewBox 單位）。SVG 文字無法在畫之前用
  // DOM API 量實際寬度，用等寬粗估即可，標籤本身不要求像素級精準，只要
  // 不明顯溢出。字級對應 styles.css 的 .sync-cursor-label text（13px），
  // 比原本的 11px 更大更好讀（使用者反映原本字級太小）。
  var SYNC_LABEL_CHAR_W = 7.6;
  var SYNC_LABEL_PAD_X = 6;
  var SYNC_LABEL_H = 18;
  var SYNC_LABEL_GAP = 3; // label 與 value 之間的間距（viewBox 單位）

  /**
   * 在單張圖的 SVG 上畫出同步游標：跨越整個繪圖高度的垂直線、（若該圖在
   * 這個邏輯 X 有值）一個圓點、以及貼在圓點旁的數值標籤（背景框＋文字，
   * 仿 Garmin 風格——每張圖各自顯示自己的數值，不集中成單一 tooltip）。
   *
   * tip 支援兩種形狀：
   *   字串（分圈圖、心率區間、逐秒曲線等）→ 整段用一般字重顯示
   *   {label, value}（跨場趨勢／身體狀況的日期類圖表）→ label（日期）用
   *     一般字重、value（數值）用粗體，兩個 <tspan> 拼在同一個 <text> 裡，
   *     讓使用者一眼就能抓到數字本身
   */
  function drawSyncCursorOnSvg(svg, meta, logicalX) {
    clearSyncCursor(svg);
    if (!meta || !meta.logicalXs || meta.logicalXs.length === 0) return;
    var idx = nearestIndex(meta.logicalXs, logicalX);
    var x = meta.xs[idx];
    var y = meta.yOf ? meta.yOf(idx) : null;
    var box = meta.box;
    var tip = meta.tips[idx];

    var layer = svgEl('g', { class: 'sync-cursor-layer' });
    layer.appendChild(svgEl('line', {
      x1: x, y1: box.top, x2: x, y2: box.top + box.height, class: 'sync-cursor-line'
    }));
    if (y !== null && y !== undefined) {
      layer.appendChild(svgEl('circle', { cx: x, cy: y, r: 3.5, class: 'sync-cursor-dot' }));
    }

    if (tip) {
      var isStructured = typeof tip === 'object';
      var displayText = isStructured ? (tip.label + ' ' + tip.value) : tip;
      var labelY = y !== null && y !== undefined ? y : box.top + 12;
      var labelW = displayText.length * SYNC_LABEL_CHAR_W + SYNC_LABEL_PAD_X * 2;
      // 貼在垂直線右側；靠近圖表右緣時翻到左側，避免超出 viewBox
      var atRightEdge = x + 6 + labelW > box.left + box.width;
      var labelX = atRightEdge ? x - 6 - labelW : x + 6;
      var labelTop = Math.min(Math.max(labelY - SYNC_LABEL_H / 2, box.top), box.top + box.height - SYNC_LABEL_H);

      var group = svgEl('g', { class: 'sync-cursor-label' });
      group.appendChild(svgEl('rect', {
        x: labelX, y: labelTop, width: labelW, height: SYNC_LABEL_H, rx: 3
      }));
      var textNode = svgEl('text', {
        x: labelX + SYNC_LABEL_PAD_X, y: labelTop + SYNC_LABEL_H / 2 + 4, 'text-anchor': 'start'
      });
      if (isStructured) {
        var labelSpan = svgEl('tspan', {});
        labelSpan.textContent = tip.label + ' ';
        var valueSpan = svgEl('tspan', { class: 'sync-cursor-label-value' });
        valueSpan.textContent = tip.value;
        textNode.appendChild(labelSpan);
        textNode.appendChild(valueSpan);
      } else {
        textNode.textContent = tip;
      }
      group.appendChild(textNode);
      layer.appendChild(group);
      void SYNC_LABEL_GAP; // 間距目前靠字串中的空白達成，保留常數供未來調整
    }

    svg.appendChild(layer); // 必須最後 append 才會蓋在折線/長條之上
  }

  /**
   * 群組同步的核心：在 startSvg 上算出目前指標對應的邏輯 X，
   * 然後對同群組「每一張」有 __syncMeta 的圖各自畫游標與數值標籤
   * （標籤直接畫在各圖內部，不使用全域浮動 tooltip）。
   *
   * 讀 svg.__syncMeta 而非 svg.querySelector('.hit').__chartHitMeta：
   * 離散模式圖表（分圈圖、週跑量長條圖）不畫額外的覆蓋矩形以免蓋掉原本
   * 逐項的 hover 命中，但仍會用 attachSyncMeta() 把 meta 直接掛在 svg 上，
   * 讓它們能「被動接收」同群組其他圖觸發的同步游標，見 charts.js 的
   * attachSyncMeta 說明。
   */
  function updateSyncGroup(container, startSvg, startMeta, clientX, clientY) {
    var pt = toSvgPoint(startSvg, clientX, clientY);
    if (!pt || startMeta.xs.length === 0) return;
    var startIdx = nearestIndex(startMeta.xs, pt.x);
    var logicalX = startMeta.logicalXs[startIdx];

    var svgs = container.querySelectorAll('.chart-svg');
    svgs.forEach(function (svg) {
      var meta = svg.__syncMeta;
      if (!meta) { clearSyncCursor(svg); return; } // 該圖無連續軸資料（如缺值狀態）
      drawSyncCursorOnSvg(svg, meta, logicalX);
    });
    hideTooltip(); // 群組同步用各圖內部標籤呈現數值，不需要全域 tooltip
  }

  /** 清掉群組內所有圖的同步游標（滑鼠離開群組時）。 */
  function clearSyncGroup(container) {
    container.querySelectorAll('.chart-svg').forEach(clearSyncCursor);
  }

  /**
   * 找出指標下方最相關的命中資料並顯示 tooltip／同步游標；找不到則隱藏。
   * 離散模式（無連續軸，如心率區間）：target 本身是帶 data-tip 的 .hit，
   *   直接顯示單圖 tooltip。
   * 連續模式：若 SVG 屬於某個同步群組，觸發整組同步；否則退回單圖 tooltip
   *   （目前 6 張圖中連續模式的都在群組內，這個分支是防禦性後備路徑）。
   */
  function handlePointerMove(evt) {
    var svg = evt.target.closest ? evt.target.closest('.chart-svg') : null;
    // ⚠️ 指標離開圖表區域（不論是離開整個委派容器，或只是移到同一圖表的
    // 座標軸標籤／圖表間的留白）都要清掉同步游標，不能只清全域 tooltip——
    // 否則游標線會殘留在畫面上直到下次進入另一個群組才被覆蓋掉。
    if (!svg) { hideAllSyncCursors(); return; }

    if (evt.target.classList && evt.target.classList.contains('hit')) {
      var meta = evt.target.__chartHitMeta;
      if (meta) {
        var container = findSyncGroupContainer(svg);
        if (container) {
          updateSyncGroup(container, svg, meta, evt.clientX, evt.clientY);
        } else {
          var pt = toSvgPoint(svg, evt.clientX, evt.clientY);
          if (!pt) { hideAllSyncCursors(); return; }
          var idx = nearestIndex(meta.xs, pt.x);
          showTooltipText(tipToText(meta.tips[idx]), evt.clientX, evt.clientY);
        }
        return;
      }
      var tip = evt.target.getAttribute('data-tip');
      if (tip) {
        showTooltipText(tip, evt.clientX, evt.clientY);
        return;
      }
    }
    hideAllSyncCursors();
  }

  /** 滑鼠離開整個委派容器時：隱藏 tooltip 並清掉所有群組的同步游標。 */
  function hideAllSyncCursors() {
    hideTooltip();
    SYNC_GROUPS.forEach(function (id) {
      var container = $(id);
      if (container) clearSyncGroup(container);
    });
  }

  /**
   * 事件委派只綁一次（在 init() 呼叫），resize 造成的整批重繪不需要重綁。
   * 用 Pointer Events 讓滑鼠與觸控共用同一套邏輯：
   *   pointermove  → 滑鼠 hover 顯示/更新 tooltip 與同步游標
   *   pointerdown  → 觸控裝置的「點一下顯示」（沒有 hover 概念）
   *   pointerleave → 滑鼠離開整個委派容器時隱藏
   *
   * ⚠️ 觸控裝置 tap 結束後緊接著會觸發 pointerleave（target 為 null，
   * 代表指標已離開追蹤範圍）——這是瀏覽器對觸控互動的正常行為，不是
   * 使用者主動把手指移開。實測（Playwright 觸控模擬）確認：tap 一個
   * .hit 矩形後，事件序列是 pointerdown → pointerup → pointerleave，
   * 若 pointerleave 無條件呼叫 hideTooltip，剛顯示的 tooltip 會在同一
   * frame 內被立即關閉，等於「點一下顯示」完全失效。故 pointerleave
   * 只在滑鼠（非觸控）時才隱藏；觸控裝置改成點擊頁面其他地方（非
   * .hit）時才隱藏，讓 tooltip 停留到使用者主動點開下一個資料點或點別處。
   */
  function bindTooltipDelegation() {
    document.body.addEventListener('pointermove', function (evt) {
      if (evt.pointerType === 'touch') return; // 觸控用 pointerdown 顯示，避免滑動時一直跳
      handlePointerMove(evt);
    });
    document.body.addEventListener('pointerdown', function (evt) {
      if (evt.pointerType !== 'touch') return;
      var isHit = evt.target.classList && evt.target.classList.contains('hit');
      if (isHit) { handlePointerMove(evt); } else { hideAllSyncCursors(); }
    });
    document.body.addEventListener('pointerleave', function (evt) {
      if (evt.pointerType === 'touch') return; // 觸控的「離開」是 tap 結束的正常訊號，不應隱藏
      hideAllSyncCursors();
    });
  }

  function fmtDelta(v, decimals, unit) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    var n = Number(v);
    var sign = n > 0 ? '+' : '';
    return sign + n.toFixed(decimals === undefined ? 0 : decimals) + (unit || '');
  }

  function deltaClass(v) {
    if (v === null || v === undefined || !isFinite(v)) return '';
    if (v > 0) return ' delta-up';
    if (v < 0) return ' delta-down';
    return '';
  }

  // ------------------------------------------------------------ 區塊一：今日
  //
  // 純呈現層：把 /api/today 的三欄位事實（數值/來源/信賴度）與課表原樣
  // 顯示出來，不做任何「今天該不該練」的判讀——判讀留給 MCP 對話中的
  // 教練（見 PLAN.md §7.3、mcp/server.py 的 coach 行為約束）。

  function renderTodayPlanRow(label, plan) {
    var row = h('div', 'today-plan-row');
    row.appendChild(h('span', 'today-plan-label', label));
    if (!plan) {
      row.appendChild(h('span', 'today-plan-value', '無課表'));
      return row;
    }
    var text = (WORKOUT_TYPE_LABEL[plan.workout_type] || plan.workout_type || '—');
    if (plan.planned_distance_km !== null && plan.planned_distance_km !== undefined && plan.planned_distance_km > 0) {
      text += '　' + C.formatNumber(plan.planned_distance_km, 1) + ' km';
    }
    if (plan.planned_pace_sec_per_km) {
      text += '　' + C.formatPace(plan.planned_pace_sec_per_km) + '/km';
    }
    row.appendChild(h('span', 'today-plan-value', text));
    if (plan.created_at) {
      row.appendChild(h('span', 'today-note', '課表版本 ' + plan.created_at.slice(0, 10)));
    }
    return row;
  }

  function renderTodayReadinessBlock(readiness) {
    var block = h('div', 'today-block');
    block.appendChild(h('div', 'today-block-title', '準備度（規則引擎）'));

    var today = readiness && readiness.today;
    if (!today || today.readiness === null || today.readiness === undefined) {
      var reasons = (today && today.not_assessed || [])
        .map(function (na) { return na.reason; });
      block.appendChild(h('div', 'state-msg state-unavailable',
        reasons.length ? reasons[0] : '今日尚無足夠資料可評估'));
    } else {
      var row = h('div', 'today-fact-row');
      row.appendChild(h('span', 'today-fact-label', '今日判定'));
      row.appendChild(h('span', 'today-fact-value', today.readiness === 'low' ? '低' : '正常'));
      block.appendChild(row);
      (today.triggers || []).forEach(function (t) {
        var trow = h('div', 'today-plan-row');
        trow.appendChild(h('span', 'today-plan-label', ''));
        var detailWrap = h('span', 'today-plan-value', t.detail);
        trow.appendChild(detailWrap);
        var level = (t.confidence && t.confidence.level) || 'high';
        trow.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
        block.appendChild(trow);
      });
    }

    var latest = readiness && readiness.latest_evaluable;
    if (latest && (!today || today.readiness === null || today.readiness === undefined)) {
      block.appendChild(h('div', 'today-note',
        '最近一次可評估：' + latest.date + '　' + (latest.readiness === 'low' ? '低' : '正常')));
    }
    return block;
  }

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
  function renderSyncFreshness(nodeId, showLabel) {
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

  function renderSidebarSync() {
    renderSyncFreshness('sidebar-sync', true);
  }

  function renderToday() {
    var node = $('today-body');
    clear(node);
    var data = state.today;
    if (!data) { showLoading(node, '載入今日狀態'); return; }

    if (data.freshness) {
      var f = data.freshness;
      var days = f.days_behind;
      // days_behind 是拿 wellness_latest 與 activity_latest 兩者中較新的
      // 日期去算的（見後端 get_today() 的 most_recent），故顯示文字必須
      // 分開列出兩者，不能只挑其中一個日期配這個天數——挑錯就會出現
      // 「顯示活動日 09-04 卻標『今天』」這種自相矛盾的畫面（曾經發生過）。
      var parts = [];
      if (f.activity_latest) parts.push('活動 ' + f.activity_latest);
      if (f.wellness_latest) parts.push('身體數據 ' + f.wellness_latest);
      var freshText = parts.length > 0
        ? '資料最後同步：' + parts.join('　·　')
          + (days === null || days === undefined ? '' : (days > 0 ? '（' + days + ' 天前）' : '（今天）'))
        : '尚無同步資料';
      node.appendChild(h('div', 'today-freshness', freshText));
    }

    renderSidebarSync();

    var grid = h('div', 'today-grid');

    // 課表卡
    var planBlock = h('div', 'today-block');
    planBlock.appendChild(h('div', 'today-block-title', '課表'));
    var planned = data.planned || {};
    planBlock.appendChild(renderTodayPlanRow('今日', planned.today));
    planBlock.appendChild(renderTodayPlanRow('明日', planned.tomorrow));
    grid.appendChild(planBlock);

    // 準備度卡
    grid.appendChild(renderTodayReadinessBlock(data.readiness));

    // 訓練負荷 + HRV 卡
    var loadBlock = h('div', 'today-block');
    loadBlock.appendChild(h('div', 'today-block-title', '訓練負荷與 HRV'));
    if (!data.load && !data.hrv) {
      loadBlock.appendChild(h('div', 'state-msg state-unavailable', '今日尚無資料可評估'));
    } else {
      if (data.load) {
        ['atl', 'ctl', 'tsb'].forEach(function (key) {
          var row = h('div', 'today-fact-row');
          row.appendChild(h('span', 'today-fact-label', key.toUpperCase()));
          row.appendChild(factValueNode(data.load[key], function (v) { return C.formatNumber(v, 1); }));
          loadBlock.appendChild(row);
        });
      }
      if (data.hrv) {
        // as_of 有值＝顯示的不是今天而是這個日期的資料（今天尚未同步）。
        // 必須明示，否則使用者會把昨天的 HRV 當成今天的。
        if (data.hrv.as_of) {
          loadBlock.appendChild(h('div', 'today-note',
            'HRV 為 ' + data.hrv.as_of + ' 的資料（今日尚未同步）'));
        }
        // 昨夜均值與 7 日均值分開列：Garmin 的狀態與平衡區間是拿 7 日均值
        // 判的（實測 39/39 天吻合，昨夜均值只有 29/39），只列昨夜值會讓人
        // 誤拿它去對平衡區間。
        var hrvRow = h('div', 'today-fact-row');
        hrvRow.appendChild(h('span', 'today-fact-label', 'HRV 昨夜均值'));
        hrvRow.appendChild(factValueNode(data.hrv.today_ms, function (v) { return C.formatNumber(v, 1); }, ' ms'));
        loadBlock.appendChild(hrvRow);
        if (data.hrv.weekly_avg_ms) {
          var weeklyRow = h('div', 'today-fact-row');
          weeklyRow.appendChild(h('span', 'today-fact-label', 'HRV 7 日均值'));
          weeklyRow.appendChild(factValueNode(data.hrv.weekly_avg_ms, function (v) { return C.formatNumber(v, 1); }, ' ms'));
          loadBlock.appendChild(weeklyRow);
        }

        // Garmin 自己的 HRV 狀態（平衡／不平衡）。
        //
        // 這一塊走的是 garmin_reference（for_display_only），不是 fact()
        // 的三欄位契約——它是手錶端黑盒演算法的輸出，規則引擎與 AI 教練
        // 都不讀它，這裡純粹是「讓你看到手錶顯示了什麼」。因此不掛信賴度
        // badge（那是 fact 的語彙），改用專屬的弱化註記說明它只能對照參考。
        var gref = data.hrv.garmin_reference;
        if (gref && gref.status) {
          var statusRow = h('div', 'today-fact-row');
          statusRow.appendChild(h('span', 'today-fact-label', 'Garmin HRV 狀態'));
          var statusVal = h('span', 'today-fact-value', null);
          var statusText = h('span', HRV_STATUS_CLASS[gref.status] || '',
            HRV_STATUS_LABEL[gref.status] || gref.status);
          statusVal.appendChild(statusText);
          var refBadge = h('span', 'conf-badge conf-reference', '僅供對照');
          refBadge.title = gref.note || '';
          statusVal.appendChild(refBadge);
          statusRow.appendChild(statusVal);
          loadBlock.appendChild(statusRow);

          // 平衡區間對照的是 7 日均值、不是昨夜值——例如昨夜 77 ms 高於
          // 47–63 ms 仍判 UNBALANCED，是 7 日均值落在區間外
          if (gref.baseline_balanced_low && gref.baseline_balanced_upper) {
            var baseRow = h('div', 'today-fact-row');
            baseRow.appendChild(h('span', 'today-fact-label', 'Garmin 平衡區間（對照 7 日均值）'));
            baseRow.appendChild(h('span', 'today-fact-value',
              C.formatNumber(gref.baseline_balanced_low, 0) + '–'
              + C.formatNumber(gref.baseline_balanced_upper, 0) + ' ms'));
            loadBlock.appendChild(baseRow);
          }
        }

        if (data.hrv.delta_pct) {
          var deltaRow = h('div', 'today-fact-row');
          deltaRow.appendChild(h('span', 'today-fact-label', '昨夜較 7 日均值'));
          deltaRow.appendChild(factValueNode(data.hrv.delta_pct, function (v) { return fmtDelta(v, 1, '%'); }));
          loadBlock.appendChild(deltaRow);
        }
      }
    }
    grid.appendChild(loadBlock);

    // 最近一場活動卡
    var lastBlock = h('div', 'today-block');
    lastBlock.appendChild(h('div', 'today-block-title', '最近一場'));
    if (!data.last_activity) {
      lastBlock.appendChild(h('div', 'state-msg state-unavailable', '尚無活動紀錄'));
    } else {
      var la = data.last_activity;
      var btn = h('button', 'session-card session-card-inline', null);
      btn.type = 'button';
      var text = la.date + '　' + (ACTIVITY_TYPE_LABEL[la.activity_type] || la.activity_type);
      if (la.distance_km) text += '　' + C.formatNumber(la.distance_km, 1) + ' km';
      if (la.avg_pace_sec_per_km) text += '　' + C.formatPace(la.avg_pace_sec_per_km) + '/km';
      btn.textContent = text;
      if (la.activity_type && RUNNING_ACTIVITY_TYPES_CLIENT.indexOf(la.activity_type) !== -1) {
        btn.addEventListener('click', function () { navigate('sessions', la.id); });
      } else {
        btn.disabled = true;
        btn.title = '非跑步活動，無單場分析頁可跳轉';
      }
      lastBlock.appendChild(btn);
    }
    grid.appendChild(lastBlock);

    node.appendChild(grid);
  }

  // 與後端 activity_types.RUNNING_ACTIVITY_TYPES 對應的前端唯讀複本——
  // 僅用於「今日卡的最近一場是否可跳轉單場分析」這個 UI 判斷，不影響
  // 任何資料篩選（篩選邏輯仍在後端 /api/sessions 完成）。
  var RUNNING_ACTIVITY_TYPES_CLIENT = ['running', 'treadmill_running', 'track_running', 'trail_running'];

  // ---------------------------------------------------- 今日頁：KPI 條

  /**
   * ⓘ 定義按鈕：點一下開一個小視窗顯示定義，按 ✕、Esc、點視窗外或捲動內容區
   * 就關閉。說明只講「這個數字是什麼、怎麼算的」，不寫好壞判讀。
   *
   * 用點擊而非 hover：手機沒有 hover。整頁共用同一個小視窗（#info-popover），
   * 開新的會取代舊的，不會疊出好幾個。
   * @param {string} label 指標名稱（小視窗標題，也是給螢幕閱讀器的按鈕名稱）
   * @param {string|string[]} text 定義文字；陣列時每個元素一段
   * @returns {HTMLElement} 按鈕
   */
  function infoTip(label, text) {
    var button = h('button', 'info-tip-btn', 'i');
    button.type = 'button';
    button.setAttribute('aria-label', label + '的定義');
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.addEventListener('click', function (evt) {
      evt.stopPropagation();
      if (infoPopoverState.button === button) { closeInfoPopover(); return; }
      openInfoPopover(button, label, Array.isArray(text) ? text : [text]);
    });
    return button;
  }

  var infoPopoverState = { el: null, button: null };

  function ensureInfoPopover() {
    if (infoPopoverState.el) return infoPopoverState.el;
    var el = h('div', 'info-popover');
    el.id = 'info-popover';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'false');
    el.setAttribute('aria-labelledby', 'info-popover-title');
    el.tabIndex = -1;
    el.hidden = true;
    var head = h('div', 'info-popover-head');
    var title = h('h4', 'info-popover-title');
    title.id = 'info-popover-title';
    head.appendChild(title);
    var close = h('button', 'info-popover-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '關閉說明');
    close.addEventListener('click', function () { closeInfoPopover(); });
    head.appendChild(close);
    el.appendChild(head);
    el.appendChild(h('div', 'info-popover-body'));
    // 點視窗內部不算「點外面」
    el.addEventListener('click', function (evt) { evt.stopPropagation(); });
    document.body.appendChild(el);
    infoPopoverState.el = el;
    return el;
  }

  /**
   * 開小視窗並定位在按鈕下方（右緣對齊按鈕）；下方放不下就翻到上方，
   * 左右與上下都夾在視窗內 12px，窄螢幕也不會超出畫面或造成橫向捲動。
   */
  function openInfoPopover(button, label, paragraphs) {
    closeInfoPopover();
    var el = ensureInfoPopover();
    el.querySelector('.info-popover-title').textContent = label;
    var body = el.querySelector('.info-popover-body');
    clear(body);
    paragraphs.forEach(function (p) { body.appendChild(h('p', null, p)); });
    el.hidden = false;

    var margin = 12;
    var vw = document.documentElement.clientWidth;
    var vh = window.innerHeight;
    var rect = button.getBoundingClientRect();
    var width = Math.min(340, vw - margin * 2);
    el.style.width = width + 'px';
    var left = Math.min(Math.max(rect.right - width, margin), vw - width - margin);
    var height = el.offsetHeight;
    var top = rect.bottom + 6;
    if (top + height > vh - margin) top = rect.top - height - 6;
    top = Math.max(margin, Math.min(top, vh - height - margin));
    el.style.left = left + 'px';
    el.style.top = top + 'px';

    infoPopoverState.button = button;
    button.setAttribute('aria-expanded', 'true');
    el.focus({ preventScroll: true });
  }

  /** @param {boolean} [restoreFocus] 是否把焦點還給按鈕（Esc／✕ 關閉時要，點別處時不要） */
  function closeInfoPopover(restoreFocus) {
    var st = infoPopoverState;
    if (!st.el || st.el.hidden) return;
    st.el.hidden = true;
    if (st.button) {
      st.button.setAttribute('aria-expanded', 'false');
      if (restoreFocus) st.button.focus({ preventScroll: true });
    }
    st.button = null;
  }

  function bindInfoTipDismiss() {
    document.addEventListener('click', function () { closeInfoPopover(false); });
    document.addEventListener('keydown', function (evt) {
      if (evt.key === 'Escape') closeInfoPopover(true);
    });
    // 小視窗是 fixed 定位，內容區一捲動就會和按鈕錯開，直接關掉
    var content = $('content');
    if (content) content.addEventListener('scroll', function () { closeInfoPopover(false); }, { passive: true });
    window.addEventListener('scroll', function () { closeInfoPopover(false); }, { passive: true });
    window.addEventListener('resize', function () { closeInfoPopover(false); });
  }

  /** 信賴度 badge（fact 的三欄位契約），hover 顯示依據來源與已知偏誤。 */
  function confBadge(fact) {
    var level = (fact && fact.confidence && fact.confidence.level) || 'high';
    var badge = h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level);
    var biases = (fact && fact.confidence && fact.confidence.biases || [])
      .map(function (b) { return b.detail; }).join('；');
    badge.title = ((fact && fact.source) || '') + (biases ? '　' + biases : '');
    return badge;
  }

  /**
   * 一張 KPI 卡：大數字 + 單位 + 下方註記 + 右側迷你趨勢線。
   * 版面參考常見跑者儀表板的「數值配縮圖」模式，但只放事實，
   * 判讀留給 AI 教練面板（本頁頁尾明載「不含任何解讀或建議」）。
   * @param {Object} cfg { label, chip, value, unit, note, series, invert, tip, badge }
   *   chip：標題旁的時間範圍小標籤（例如「90 天」「本週」），讓每個數字的涵蓋範圍一目了然
   *   tip：定義文字（有給才出現 ⓘ 按鈕，見 infoTip）
   *   badge：附在數字旁的節點（信賴度 badge 等）
   */
  function kpiCard(cfg) {
    var card = h('div', 'kpi-card');
    var labelRow = h('div', 'kpi-label');
    var labelText = h('span', null, cfg.label);
    if (cfg.chip) labelText.appendChild(h('span', 'window-chip', cfg.chip));
    labelRow.appendChild(labelText);
    if (cfg.tip) labelRow.appendChild(infoTip(cfg.label, cfg.tip));
    card.appendChild(labelRow);

    var main = h('div', 'kpi-main');
    var valWrap = h('div', 'kpi-value-wrap');
    valWrap.appendChild(h('span', 'kpi-value', cfg.value));
    if (cfg.unit) valWrap.appendChild(h('span', 'kpi-unit', cfg.unit));
    if (cfg.badge) valWrap.appendChild(cfg.badge);
    main.appendChild(valWrap);

    if (cfg.series && C.sparkline) {
      var spark = C.sparkline(cfg.series, { invert: cfg.invert });
      if (spark) {
        var sparkWrap = h('div', 'kpi-spark');
        sparkWrap.appendChild(spark);
        main.appendChild(sparkWrap);
      }
    }
    card.appendChild(main);

    if (cfg.note) card.appendChild(h('div', 'kpi-note', cfg.note));
    return card;
  }

  /** 本週（週日起算，與行事曆一致）已完成的跑步距離與本週課表總距離。 */
  function weekVolumeSummary() {
    var days = state.weekDays || [];
    var done = 0;
    var planned = 0;
    days.forEach(function (d) {
      (d.activities || []).forEach(function (a) {
        if (a.is_running && a.distance_km) done += a.distance_km;
      });
      (d.planned || []).forEach(function (p) {
        if (p.planned_distance_km) planned += p.planned_distance_km;
      });
    });
    return { done: done, planned: planned };
  }

  function renderKpiStrip() {
    var node = $('kpi-strip');
    if (!node) return;
    clear(node);

    // weekDays 與 todayKpi 是兩支不同的請求，任一支還沒回來就先顯示載入中，
    // 避免畫出一半的卡片再跳動
    if (!state.weekDays || !state.todayKpi) {
      showLoading(node, '載入近期概況');
      return;
    }

    var strip = h('div', 'kpi-strip');
    var kpi = state.todayKpi;

    // ---- 本週跑量進度
    var vol = weekVolumeSummary();
    var volCard = kpiCard({
      label: '本週跑量',
      value: C.formatNumber(vol.done, 1),
      unit: 'km',
      note: vol.planned > 0
        ? '課表 ' + C.formatNumber(vol.planned, 1) + ' km'
        : '本週無課表距離'
    });
    if (vol.planned > 0) {
      var pct = Math.min(100, (vol.done / vol.planned) * 100);
      var bar = h('div', 'kpi-bar');
      var fill = h('div', 'kpi-bar-fill');
      fill.style.width = pct.toFixed(1) + '%';
      bar.appendChild(fill);
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label',
        '本週跑量 ' + C.formatNumber(vol.done, 1) + ' km，課表 '
        + C.formatNumber(vol.planned, 1) + ' km，達成 ' + Math.round(pct) + '%');
      volCard.appendChild(bar);
      volCard.appendChild(h('div', 'kpi-note kpi-note-pct', '達成 ' + Math.round(pct) + '%'));
    }
    strip.appendChild(volCard);

    // ---- 本週跑步次數
    var runCount = 0;
    (state.weekDays || []).forEach(function (d) {
      (d.activities || []).forEach(function (a) { if (a.is_running) runCount += 1; });
    });
    strip.appendChild(kpiCard({
      label: '本週跑步次數',
      value: String(runCount),
      unit: '次',
      note: '週日起算'
    }));

    // ---- TSB（訓練壓力平衡）
    var loadSeries = kpi.loadSeries || [];
    var tsbVals = loadSeries.map(function (p) {
      return p.tsb && typeof p.tsb.value === 'number' ? p.tsb.value : null;
    }).filter(function (v) { return v !== null; });
    if (tsbVals.length > 0) {
      var lastTsb = tsbVals[tsbVals.length - 1];
      strip.appendChild(kpiCard({
        label: 'TSB（CTL − ATL）',
        value: (lastTsb > 0 ? '+' : '') + C.formatNumber(lastTsb, 1),
        note: '近 30 天走勢',
        series: tsbVals
      }));
    }

    // ---- HRV
    var hrvVals = (kpi.hrvPoints || []).map(function (p) { return p.value; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    if (hrvVals.length > 0) {
      strip.appendChild(kpiCard({
        label: 'HRV 夜間均值',
        value: C.formatNumber(hrvVals[hrvVals.length - 1], 0),
        unit: 'ms',
        note: '近 30 天走勢',
        series: hrvVals
      }));
    }

    // ---- 靜止心率（越低越好，故 invert）
    var rhrVals = (kpi.rhrPoints || []).map(function (p) { return p.value; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    if (rhrVals.length > 0) {
      strip.appendChild(kpiCard({
        label: '靜止心率',
        value: C.formatNumber(rhrVals[rhrVals.length - 1], 0),
        unit: 'bpm',
        note: '近 30 天走勢',
        series: rhrVals,
        invert: true
      }));
    }

    node.appendChild(strip);
  }

  /**
   * KPI 條要的資料散在兩支既有端點，這裡併發取回後一起渲染。
   * 固定用 30d：今日頁不吃頂列 range，迷你趨勢線也只需要「最近的走勢」。
   * 任一支失敗就當該卡沒資料（catch 回 null），不讓整條 KPI 消失。
   */
  function loadTodayKpi() {
    var q = state.athleteId ? '&athlete_id=' + state.athleteId : '';
    Promise.all([
      api('/api/training-load?range=30d' + q).catch(function () { return null; }),
      api('/api/wellness-trend?range=30d' + q).catch(function () { return null; })
    ]).then(function (res) {
      var load = res[0];
      var wellness = res[1];
      var metrics = (wellness && wellness.metrics) || {};
      state.todayKpi = {
        loadSeries: (load && load.series) || [],
        hrvPoints: (metrics.hrv_ms && metrics.hrv_ms.points) || [],
        rhrPoints: (metrics.resting_hr_bpm && metrics.resting_hr_bpm.points) || []
      };
      renderKpiStrip();
    });
  }

  // ------------------------------------------- 今日頁：本週課表一覽

  function renderWeekPlanList() {
    var node = $('week-plan-list');
    if (!node) return;
    clear(node);
    if (!state.weekDays) return;

    var list = h('div', 'week-plan-list');
    state.weekDays.forEach(function (day) {
      var row = h('div', 'wp-row day-' + day.day_state);
      row.appendChild(h('span', 'wp-dow', dowLabel(day.date)));
      row.appendChild(h('span', 'wp-date', day.date.slice(5)));

      var planText = '\u2014';
      if (day.planned && day.planned.length > 0) {
        var pl = day.planned[0];
        planText = WORKOUT_TYPE_LABEL[pl.workout_type] || pl.workout_type;
        if (pl.planned_distance_km) planText += ' ' + C.formatNumber(pl.planned_distance_km, 1) + ' km';
      }
      row.appendChild(h('span', 'wp-plan', planText));

      // 實際：把當天所有跑步距離加總，其餘類型只標類型名
      var runKm = 0;
      var otherTypes = [];
      (day.activities || []).forEach(function (a) {
        if (a.is_running && a.distance_km) runKm += a.distance_km;
        else if (!a.is_running) otherTypes.push(ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type);
      });
      var actParts = [];
      if (runKm > 0) actParts.push(C.formatNumber(runKm, 1) + ' km');
      if (otherTypes.length > 0) actParts.push(otherTypes.join('、'));
      row.appendChild(h('span', 'wp-actual', actParts.length > 0 ? actParts.join('　') : '\u2014'));

      row.appendChild(h('span', 'wp-state', DAY_STATE_LABEL[day.day_state] || day.day_state));
      list.appendChild(row);
    });
    node.appendChild(list);
  }

  // ------------------------------------------- 今日頁：最近訓練清單

  function renderRecentSessions() {
    var node = $('recent-sessions');
    if (!node) return;
    clear(node);
    if (!state.recentSessions) { showLoading(node, '載入最近訓練'); return; }
    if (state.recentSessions.length === 0) {
      node.appendChild(h('div', 'state-msg state-unavailable', '近 30 天沒有跑步場次'));
      return;
    }

    var list = h('div', 'recent-list');
    state.recentSessions.slice(0, 8).forEach(function (s) {
      var row = h('button', 'recent-row', null);
      row.type = 'button';
      row.addEventListener('click', function () { navigate('sessions', s.id); });

      row.appendChild(h('span', 'rc-date', s.date.slice(5)));
      row.appendChild(h('span', 'rc-type', WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type || '未分類'));
      row.appendChild(h('span', 'rc-km', s.distance_km ? C.formatNumber(s.distance_km, 2) + ' km' : '\u2014'));
      row.appendChild(h('span', 'rc-pace', s.avg_pace_sec_per_km ? C.formatPace(s.avg_pace_sec_per_km) + '/km' : '\u2014'));
      row.appendChild(h('span', 'rc-hr', s.avg_hr_bpm ? s.avg_hr_bpm + ' bpm' : '\u2014'));
      list.appendChild(row);
    });
    node.appendChild(list);
  }

  function loadRecentSessions() {
    var q = state.athleteId ? '&athlete_id=' + state.athleteId : '';
    api('/api/sessions?range=30d' + q).then(function (data) {
      state.recentSessions = (data && data.sessions) || [];
      renderRecentSessions();
    }).catch(function (err) {
      showError($('recent-sessions'), err.message);
    });
  }

  function loadToday() {
    showLoading($('today-body'), '載入今日狀態');
    api('/api/today').then(function (data) {
      state.today = data;
      renderToday();
      // 設定頁的「最後同步」與側邊列同源，資料回來時一併更新（設定頁此刻
      // 可能不在畫面上，但它沒有圖表、不吃容器寬度，直接寫入即可）
      renderSettingsSync();
    }).catch(function (err) {
      showError($('today-body'), err.message);
    });
  }

  // ------------------------------------------------------------ 區塊三：單場分析

  function renderSessionPicker() {
    var node = $('session-picker');
    clear(node);
    if (state.sessions.length === 0) {
      node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步場次'));
      return;
    }
    var select = h('select', 'session-select');
    select.setAttribute('aria-label', '選擇要分析的場次');
    state.sessions.forEach(function (s) {
      var opt = document.createElement('option');
      opt.value = String(s.id);
      opt.textContent = s.date + '　' + C.formatNumber(s.distance_km, 2) + ' km　'
        + (s.workout_type ? (WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type) : '未標記');
      if (s.id === state.selectedSessionId) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener('change', function () {
      selectSession(parseInt(select.value, 10));
    });
    node.appendChild(select);
  }

  function renderSessionSummary(detail) {
    var node = $('session-summary');
    clear(node);
    var s = detail.summary || {};

    var head = h('div', 'session-head');
    var titleWrap = h('div', 'session-title-wrap');
    titleWrap.appendChild(h('h3', 'session-title', detail.title || '（無標題）'));
    titleWrap.appendChild(h('div', 'session-meta',
      (detail.started_at || '').replace('T', ' ') + '　・　' + (detail.activity_type || '—')));
    head.appendChild(titleWrap);

    // 訓練類型標記（含 auto/manual 來源與計畫類型對照）
    var tags = h('div', 'tag-row');
    if (detail.workout_type) {
      var tag = h('span', 'tag tag-type tag-type-' + detail.workout_type,
        WORKOUT_TYPE_LABEL[detail.workout_type] || detail.workout_type);
      tags.appendChild(tag);
      if (detail.workout_type_source) {
        tags.appendChild(h('span', 'tag tag-source',
          SOURCE_LABEL[detail.workout_type_source] || detail.workout_type_source));
      }
    } else {
      tags.appendChild(h('span', 'tag tag-muted', '訓練類型：未標記'));
    }
    if (detail.planned) {
      var plannedText = '計畫：'
        + (detail.planned.workout_type
          ? (WORKOUT_TYPE_LABEL[detail.planned.workout_type] || detail.planned.workout_type)
          : '—');
      if (detail.planned.planned_distance_km !== null && detail.planned.planned_distance_km !== undefined) {
        plannedText += ' ' + C.formatNumber(detail.planned.planned_distance_km, 1) + ' km';
      }
      tags.appendChild(h('span', 'tag tag-planned', plannedText));
    } else {
      tags.appendChild(h('span', 'tag tag-muted', '無對應計畫'));
    }
    head.appendChild(tags);
    node.appendChild(head);

    var grid = h('div', 'stat-grid');
    grid.appendChild(statCard('距離', C.formatNumber(s.distance_km, 2), 'km'));
    grid.appendChild(statCard('時間', C.formatDuration(s.duration_sec), ''));
    grid.appendChild(statCard('平均配速', C.formatPace(s.avg_pace_sec_per_km), '/km'));
    grid.appendChild(statCard('平均心率', C.formatNumber(s.avg_hr_bpm, 0), 'bpm'));
    grid.appendChild(statCard('最大心率', C.formatNumber(s.max_hr_bpm, 0), 'bpm'));
    grid.appendChild(statCard('平均步頻', C.formatNumber(s.avg_cadence_spm, 0), 'spm'));
    grid.appendChild(statCard('有氧 TE', C.formatNumber(s.aerobic_te, 1), ''));
    grid.appendChild(statCard('爬升', C.formatNumber(s.elevation_gain_m, 0), 'm'));
    grid.appendChild(statCard('熱量', C.formatNumber(s.calories, 0), 'kcal'));
    node.appendChild(grid);
  }

  function renderHrDrift(detail) {
    var node = $('hr-drift');
    clear(node);
    var drift = detail.hr_drift;
    if (!drift || drift.available === false) {
      showUnavailable(node, drift && drift.reason ? drift.reason : '無心率漂移資料');
      return;
    }
    var grid = h('div', 'stat-grid stat-grid-3');
    grid.appendChild(statCard('前半段平均心率', C.formatNumber(drift.first_half_avg_hr, 0), 'bpm'));
    grid.appendChild(statCard('後半段平均心率', C.formatNumber(drift.second_half_avg_hr, 0), 'bpm'));
    grid.appendChild(statCard('心率漂移', C.formatNumber(drift.drift_pct, 1) + '%', ''));
    node.appendChild(grid);
  }

  function renderLaps(detail) {
    var node = $('lap-chart');
    clear(node);
    var laps = detail.laps;
    if (!laps || laps.available === false) {
      showUnavailable(node, laps && laps.reason ? laps.reason : '此活動無分圈資料');
      return;
    }
    var list = laps.laps || [];
    if (list.length === 0) {
      showUnavailable(node, '此活動無分圈資料');
      return;
    }
    if (laps.source) {
      var src = h('div', 'chart-note',
        '資料來源：' + (laps.source === 'fit' ? 'FIT 每公里分圈' : '裝置手動分圈'));
      node.appendChild(src);
    }
    node.appendChild(wrapChart(C.lapPaceChart(list, { containerWidth: widthOf(node) })));
  }

  function renderRecords(detail) {
    var node = $('records-chart');
    clear(node);
    var records = detail.records;
    if (!records || records.available === false) {
      showUnavailable(node, records && records.reason ? records.reason : '此活動尚未解析逐秒資料');
      return;
    }
    var points = records.points || [];
    if (points.length === 0) {
      showUnavailable(node, '此活動尚未解析逐秒資料');
      return;
    }
    var legend = h('div', 'legend');
    var l1 = h('span', 'legend-item');
    l1.appendChild(h('span', 'swatch swatch-pace'));
    l1.appendChild(h('span', null, '配速（左軸，上快下慢）'));
    var l2 = h('span', 'legend-item');
    l2.appendChild(h('span', 'swatch swatch-hr'));
    l2.appendChild(h('span', null, '心率（右軸）'));
    legend.appendChild(l1);
    legend.appendChild(l2);
    node.appendChild(legend);
    if (records.sample_every_sec) {
      node.appendChild(h('div', 'chart-note', '取樣間隔：每 ' + records.sample_every_sec + ' 秒一筆'));
    }
    node.appendChild(wrapChart(C.recordsChart(points, { containerWidth: widthOf(node) })));
  }

  /**
   * 心率區間分布。zones.zones 固定回傳 Z1~Z5 共 5 列（後端保證，即使某場
   * 沒進過某區間也是 seconds=0 照樣列出）——跨場次比較時圖表軸線才會一致，
   * 不因某場強度較低就少幾列。below_zone_1（低於 Z1 的暖身時間）同樣固定
   * 顯示在圖表下方，沒有暖身時間就顯示 0，而不是整行隱藏。
   */
  function renderHrZones(detail) {
    var node = $('hrzone-chart');
    clear(node);
    var zones = detail.hr_zones;
    if (!zones || zones.available === false) {
      showUnavailable(node, zones && zones.reason ? zones.reason : '此活動無心率區間資料');
      return;
    }
    node.appendChild(wrapChart(C.hrZoneChart(zones.zones, { containerWidth: widthOf(node) })));
    var below = zones.below_zone_1;
    if (below) {
      node.appendChild(h('div', 'chart-note',
        '低於 Zone 1（暖身）：' + C.formatDuration(below.seconds)
        + '（' + below.pct.toFixed(0) + '%）'));
    }
  }

  /** 該場的隔天恢復影響：只顯示數字卡，不畫圖、不做解讀 */
  function renderRecoveryImpact() {
    var node = $('recovery-impact');
    clear(node);
    if (state.selectedSessionId === null) {
      showUnavailable(node, '尚未選擇場次');
      return;
    }
    var impact = null;
    for (var i = 0; i < state.recoveryImpacts.length; i += 1) {
      if (state.recoveryImpacts[i].activity_id === state.selectedSessionId) {
        impact = state.recoveryImpacts[i];
        break;
      }
    }
    if (!impact) {
      showUnavailable(node, '目前區間內沒有這場的恢復關聯資料');
      return;
    }
    var nextDay = impact.next_day;
    if (!nextDay || nextDay.available === false) {
      showUnavailable(node, (nextDay && nextDay.reason)
        ? nextDay.reason
        : '隔天沒有身體狀況資料');
      return;
    }
    node.appendChild(h('div', 'chart-note',
      '訓練日 ' + impact.training_date + ' → 隔天 ' + nextDay.date + '（數值為隔天減訓練當天）'));

    var grid = h('div', 'stat-grid stat-grid-3');
    var hrvCard = statCard('HRV 夜間均值變化',
      fmtDelta(nextDay.hrv_delta, 1, ' ms'),
      nextDay.hrv_delta_pct === null || nextDay.hrv_delta_pct === undefined
        ? '' : fmtDelta(nextDay.hrv_delta_pct, 1, '%'));
    hrvCard.className += deltaClass(nextDay.hrv_delta);
    grid.appendChild(hrvCard);

    var rhrCard = statCard('靜止心率變化', fmtDelta(nextDay.resting_hr_delta, 0, ' bpm'), '');
    rhrCard.className += deltaClass(nextDay.resting_hr_delta);
    grid.appendChild(rhrCard);

    var readyCard = statCard('訓練準備度變化', fmtDelta(nextDay.training_readiness_delta, 0, ''), '');
    readyCard.className += deltaClass(nextDay.training_readiness_delta);
    grid.appendChild(readyCard);

    node.appendChild(grid);
  }

  function renderSessionDetail() {
    var detail = state.sessionDetail;
    if (!detail) return;
    renderSessionSummary(detail);
    renderLaps(detail);
    renderRecords(detail);
    renderHrDrift(detail);
    renderHrZones(detail);
    renderRecoveryImpact();
  }

  // ------------------------------------------------------------ 區塊二：本週與行事曆
  //
  // 週條與行事曆共用 /api/calendar（start/end 起訖日），刻意不走 range
  // 系統——這裡永遠錨定真實今天，與其他 range 圖表錨定「資料庫最新日」
  // 的語意不同（見後端 dashboard_queries.get_calendar 說明）。

  /**
   * 取該日期所在週的週日（UTC）。行事曆與本週條的一週起點。
   *
   * 【為什麼與 weeklyVolume() 的週一不一致，是刻意的】
   * 「一週從哪天開始」在本專案有兩個不同語意：
   *   - 行事曆呈現：跟隨使用者習慣的日曆排法（週日為首），純粹是看的方式
   *   - 週跑量統計：訓練科學慣例用 ISO 週（週一起算），換算週負荷、
   *     ACWR 都以此為準，改動會讓歷史數字與既有規則引擎對不上
   * 兩者各自保留自己的函式，不要為了「統一」而把其中一邊改掉。
   */
  function sundayOf(dateStr) {
    var d = new Date(dateStr + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - d.getUTCDay()); // getUTCDay() 0=週日，直接回推
    return d;
  }

  /** 取該日期所在 ISO 週的週一（UTC）。僅供週跑量等訓練負荷統計使用。 */
  function mondayOf(dateStr) {
    var d = new Date(dateStr + 'T00:00:00Z');
    var dow = d.getUTCDay();
    var offset = dow === 0 ? 6 : dow - 1;
    d.setUTCDate(d.getUTCDate() - offset);
    return d;
  }

  function addDaysISO(dateStr, days) {
    var d = new Date(dateStr + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function todayISO() {
    return new Date().toISOString().slice(0, 10);
  }

  function dowLabel(dateStr) {
    var names = ['日', '一', '二', '三', '四', '五', '六'];
    return names[new Date(dateStr + 'T00:00:00Z').getUTCDay()];
  }

  /** 建立一格週條／行事曆格內的活動色點列（全部運動類型，見 get_calendar）。 */
  function activityDots(activities) {
    var wrap = h('div', 'week-cell-dots');
    activities.forEach(function (a) {
      var dot = h('span', 'activity-dot');
      dot.style.background = 'var(' + activityColorVar(a.activity_type) + ')';
      dot.title = (ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type)
        + (a.distance_km ? '　' + C.formatNumber(a.distance_km, 1) + ' km' : '');
      wrap.appendChild(dot);
    });
    return wrap;
  }

  function firstRunningActivity(activities) {
    for (var i = 0; i < activities.length; i += 1) {
      if (activities[i].is_running) return activities[i];
    }
    return null;
  }

  function renderWeekStrip() {
    var node = $('week-strip');
    clear(node);
    if (!state.weekDays) { showLoading(node, '載入本週'); return; }

    var grid = h('div', 'week-strip');
    state.weekDays.forEach(function (day) {
      var cell = h('div', 'week-cell day-' + day.day_state);
      var runnable = firstRunningActivity(day.activities);
      if (runnable) {
        cell.classList.add('is-clickable');
        cell.addEventListener('click', function () {
          navigate('sessions', runnable.id);
        });
      }
      cell.appendChild(h('span', 'week-cell-dow', dowLabel(day.date)));
      cell.appendChild(h('span', 'week-cell-date', day.date.slice(8, 10)));
      if (day.planned.length > 0) {
        var wt = day.planned[0].workout_type;
        cell.appendChild(h('span', 'week-cell-workout', WORKOUT_TYPE_LABEL[wt] || wt));
      }
      if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
      cell.title = day.date + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
      grid.appendChild(cell);
    });
    node.appendChild(grid);
  }

  function loadWeekStrip() {
    var weekStart = sundayOf(todayISO());
    var start = weekStart.toISOString().slice(0, 10);
    var sunday = addDaysISO(start, 6); // 週日 + 6 天 = 週六，本週的最後一天
    api('/api/calendar?start=' + start + '&end=' + sunday).then(function (data) {
      state.weekDays = data.days || [];
      renderWeekStrip();
      // 本週課表一覽與 KPI 的「本週跑量」都吃同一份 weekDays，
      // 不另發請求，資料回來時一併更新
      renderWeekPlanList();
      renderKpiStrip();
    }).catch(function (err) {
      showError($('week-strip'), err.message);
    });
  }

  /** 行事曆展開時用的月份邊界（YYYY-MM 的第一天所在週日 ~ 最後一天所在週六）。 */
  function monthGridRange(yearMonth) {
    var firstOfMonth = yearMonth + '-01';
    var d = new Date(firstOfMonth + 'T00:00:00Z');
    var nextMonth = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
    var lastOfMonth = new Date(nextMonth.getTime() - 86400000).toISOString().slice(0, 10);
    return {
      start: sundayOf(firstOfMonth).toISOString().slice(0, 10),
      end: (function () {
        var lastRowStart = sundayOf(lastOfMonth);
        lastRowStart.setUTCDate(lastRowStart.getUTCDate() + 6); // 該週的週六
        return lastRowStart.toISOString().slice(0, 10);
      })(),
      monthStart: firstOfMonth,
      monthEnd: lastOfMonth
    };
  }

  function renderCalendarBody() {
    var node = $('calendar-body');
    clear(node);
    if (!state.calendarData) { showLoading(node, '載入月曆'); return; }

    var range = monthGridRange(state.calendarMonth);
    var nav = h('div', 'calendar-nav');
    var prevBtn = h('button', 'calendar-nav-btn', '‹');
    prevBtn.type = 'button';
    prevBtn.addEventListener('click', function () { shiftCalendarMonth(-1); });
    var label = h('span', 'calendar-nav-label', state.calendarMonth);
    var nextBtn = h('button', 'calendar-nav-btn', '›');
    nextBtn.type = 'button';
    nextBtn.addEventListener('click', function () { shiftCalendarMonth(1); });
    nav.appendChild(prevBtn);
    nav.appendChild(label);
    nav.appendChild(nextBtn);
    node.appendChild(nav);

    // 星期標頭。原本月曆只有日期數字、沒有星期列，改成週日為首後更需要
    // 明示欄位對應的星期，否則使用者無法確認第一欄到底是週日還是週一。
    var dowHead = h('div', 'calendar-dow-head');
    ['日', '一', '二', '三', '四', '五', '六'].forEach(function (name) {
      dowHead.appendChild(h('span', 'calendar-dow', name));
    });
    node.appendChild(dowHead);

    var grid = h('div', 'calendar-grid');
    var byDate = {};
    (state.calendarData.days || []).forEach(function (d) { byDate[d.date] = d; });

    var cursor = range.start;
    while (cursor <= range.end) {
      var day = byDate[cursor];
      var cell = h('div', 'calendar-cell' + (day ? ' day-' + day.day_state : ''));
      if (cursor < range.monthStart || cursor > range.monthEnd) cell.classList.add('is-outside-month');
      cell.appendChild(h('span', 'calendar-cell-date', cursor.slice(8, 10)));
      if (day) {
        // 只要當天有任何活動或課表就可點（不限跑步）——點擊是「看這天發生什麼」，
        // 不再是「跳到某一場跑步的明細」，所以健走、重訓、純課表日也該能點開。
        if (day.activities.length > 0 || day.planned.length > 0) {
          cell.classList.add('is-clickable');
          cell.setAttribute('role', 'button');
          cell.setAttribute('tabindex', '0');
          if (cursor === state.calendarSelectedDate) cell.classList.add('is-selected');
          (function (date) {
            cell.addEventListener('click', function () { selectCalendarDate(date); });
            // 鍵盤操作：格子是 div，不像 button 會自動把 Enter／Space 轉成 click
            cell.addEventListener('keydown', function (ev) {
              if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                selectCalendarDate(date);
              }
            });
          }(cursor));
        }
        if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
        cell.title = cursor + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
      }
      grid.appendChild(cell);
      cursor = addDaysISO(cursor, 1);
    }
    node.appendChild(grid);

    // 月曆重畫後明細要跟著更新（換月、或首次載入都會走到這裡）
    renderCalendarDay();
  }

  /**
   * 選取月曆上的某一天：更新選取態並就地渲染下方明細。
   * 刻意不改 hash——當日明細是行事曆的一部分，不是另一個位置；
   * 換月份時 renderCalendarBody 會重畫格子，選取態靠 state 保留。
   * @param {string} date 'YYYY-MM-DD'
   */
  function selectCalendarDate(date) {
    // 再點一次同一天＝收起，符合「點開／點關」的直覺
    state.calendarSelectedDate = (state.calendarSelectedDate === date) ? null : date;

    var grid = $('calendar-body').querySelector('.calendar-grid');
    if (grid) {
      var cells = grid.querySelectorAll('.calendar-cell');
      Array.prototype.forEach.call(cells, function (c) { c.classList.remove('is-selected'); });
    }
    if (state.calendarSelectedDate) {
      // 重新標記選取的格子。格子沒帶 data-date，用與繪製時相同的日期序走訪
      // 太繞，改用 title 前綴比對（title 的開頭就是 ISO 日期，見 renderCalendarBody）。
      var target = null;
      Array.prototype.forEach.call(grid ? grid.querySelectorAll('.calendar-cell') : [], function (c) {
        if (c.title && c.title.indexOf(state.calendarSelectedDate) === 0) target = c;
      });
      if (target) target.classList.add('is-selected');
    }
    renderCalendarDay();
  }

  /** 把某一天的課表與活動渲染到月曆下方的明細面板。 */
  function renderCalendarDay() {
    var panel = $('panel-calendar-day');
    var node = $('calendar-day-body');
    if (!panel || !node) return;
    clear(node);

    var date = state.calendarSelectedDate;
    if (!date) {
      panel.classList.remove('is-open');
      node.appendChild(h('p', 'calendar-day-empty', '點選月曆上的日期，這裡會顯示當天的課表與訓練內容。'));
      return;
    }
    panel.classList.add('is-open');

    var day = null;
    (state.calendarData && state.calendarData.days ? state.calendarData.days : []).forEach(function (d) {
      if (d.date === date) day = d;
    });
    if (!day) {
      node.appendChild(h('p', 'calendar-day-empty', date + '　沒有資料。'));
      return;
    }

    var head = h('div', 'calendar-day-head');
    head.appendChild(h('h3', 'calendar-day-title', date + '（' + dowLabel(date) + '）'));
    head.appendChild(h('span', 'calendar-day-state day-' + day.day_state,
      DAY_STATE_LABEL[day.day_state] || day.day_state));
    node.appendChild(head);

    // ---- 課表
    if (day.planned && day.planned.length > 0) {
      var psec = h('div', 'calendar-day-section');
      psec.appendChild(h('h4', 'calendar-day-subtitle', '課表'));
      day.planned.forEach(function (pl) {
        var row = h('div', 'calendar-day-row');
        row.appendChild(h('span', 'cd-type', WORKOUT_TYPE_LABEL[pl.workout_type] || pl.workout_type));
        if (pl.planned_distance_km) {
          row.appendChild(h('span', 'cd-val', C.formatNumber(pl.planned_distance_km, 1) + ' km'));
        }
        if (pl.planned_pace_sec_per_km) {
          row.appendChild(h('span', 'cd-val', C.formatPace(pl.planned_pace_sec_per_km) + '/km'));
        }
        psec.appendChild(row);
      });
      node.appendChild(psec);
    }

    // ---- 實際活動
    if (day.activities && day.activities.length > 0) {
      var asec = h('div', 'calendar-day-section');
      asec.appendChild(h('h4', 'calendar-day-subtitle', '實際訓練'));
      day.activities.forEach(function (a) {
        var row = h('div', 'calendar-day-row');
        var dot = h('span', 'cd-dot');
        dot.style.background = 'var(' + activityColorVar(a.activity_type) + ')';
        row.appendChild(dot);
        row.appendChild(h('span', 'cd-type', ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type));
        if (a.title) row.appendChild(h('span', 'cd-title', a.title));
        if (a.distance_km) row.appendChild(h('span', 'cd-val', C.formatNumber(a.distance_km, 2) + ' km'));
        if (a.duration_sec) row.appendChild(h('span', 'cd-val', C.formatDuration(a.duration_sec)));
        if (a.distance_km && a.duration_sec) {
          row.appendChild(h('span', 'cd-val', C.formatPace(a.duration_sec / a.distance_km) + '/km'));
        }
        // 跑步才有單場明細可看：其餘類型（健走、重訓）在訓練紀錄裡沒有對應頁面
        if (a.is_running) {
          var link = h('button', 'cd-link', '看單場分析 →');
          link.type = 'button';
          link.addEventListener('click', function () { navigate('sessions', a.id); });
          row.appendChild(link);
        }
        asec.appendChild(row);
      });
      node.appendChild(asec);
    }

    if ((!day.planned || day.planned.length === 0) && (!day.activities || day.activities.length === 0)) {
      node.appendChild(h('p', 'calendar-day-empty', '這天沒有課表也沒有訓練紀錄。'));
    }
  }

  function loadCalendarBody() {
    var range = monthGridRange(state.calendarMonth);
    api('/api/calendar?start=' + range.start + '&end=' + range.end).then(function (data) {
      state.calendarData = data;
      renderOrDefer('calendar', renderCalendarBody);
    }).catch(function (err) {
      showError($('calendar-body'), err.message);
    });
  }

  function shiftCalendarMonth(delta) {
    var d = new Date(state.calendarMonth + '-01T00:00:00Z');
    d.setUTCMonth(d.getUTCMonth() + delta);
    // 換月後原本選的那天已不在畫面上，明細跟著收起，避免月曆顯示 10 月
    // 下方卻還停在 9 月某天的內容
    state.calendarSelectedDate = null;
    navigate('calendar', d.toISOString().slice(0, 7));
  }

  function renderCalendarLegend() {
    var node = $('calendar-legend');
    clear(node);
    Object.keys(DAY_STATE_LABEL).forEach(function (key) {
      if (key === 'future') return;
      var item = h('span', 'legend-item');
      item.appendChild(h('span', 'legend-swatch day-' + key));
      item.appendChild(document.createTextNode(DAY_STATE_LABEL[key]));
      node.appendChild(item);
    });
  }

  // ------------------------------------------------------------ 區塊四：訓練負荷

  /**
   * 把 /api/training-load 的 fact 序列（{value, source, confidence}）
   * 轉成 charts.trendLineChart() 要的簡單 {date, value} 序列。信賴度
   * badge 顯示在圖表下方的說明列（TSB 每天信賴度理論上相同，逐點畫
   * badge 會過度擁擠），不逐點畫在圖上——這是折線圖已有的既有限制
   * （trendLineChart 不支援逐點 badge），非本次新增的缺陷。
   */
  function loadSeriesToChartSeries(series, key) {
    return series.map(function (day) {
      var fact = day[key];
      return { date: day.date, value: fact ? fact.value : null };
    });
  }

  function renderLoad() {
    var node = $('load-chart');
    clear(node);
    var series = state.loadSeries;
    if (!series || series.series.length === 0) {
      node.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
      return;
    }
    var rows = series.series;
    var hasAny = rows.some(function (r) { return r.tsb && r.tsb.value !== null; });
    var card = h('div', 'chart-block');
    card.appendChild(h('h3', 'chart-title', 'TSB（訓練壓力平衡，CTL − ATL）'));
    if (!hasAny) {
      card.appendChild(h('div', 'state-msg state-unavailable', '此區間內無訓練負荷資料'));
    } else {
      card.appendChild(wrapChart(C.trendLineChart(loadSeriesToChartSeries(rows, 'tsb'), {
        label: 'TSB',
        formatter: function (v) { return C.formatNumber(v, 1); },
        lineClass: 'line-te',
        dotClass: 'dot-te',
        containerWidth: widthOf(node)
      })));
      var firstConf = rows.map(function (r) { return r.tsb; }).filter(Boolean)[0];
      if (firstConf) {
        var level = (firstConf.confidence && firstConf.confidence.level) || 'high';
        var note = h('div', 'chart-note');
        note.appendChild(document.createTextNode(firstConf.source + '　'));
        note.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
        card.appendChild(note);
      }
    }
    node.appendChild(card);
  }

  function loadLoadSeries() {
    showLoading($('load-chart'), '載入訓練負荷');
    api('/api/training-load?range=' + state.range).then(function (data) {
      state.loadSeries = data;
      renderOrDefer('load', renderLoad);
      renderOrDefer('overview', renderOverview); // 總覽的 ATL/CTL/TSB 卡吃同一份
    }).catch(function (err) {
      showError($('load-chart'), err.message);
    });
  }

  // ------------------------------------------------------------ 區塊四之二：跨場趨勢

  /** 以場次日期建立趨勢資料（由舊到新） */
  function seriesFromSessions(field) {
    return state.sessions.slice().reverse().map(function (s) {
      var v = s[field];
      return { date: s.date, value: (typeof v === 'number' && isFinite(v)) ? v : null };
    });
  }

  /** 計算週跑量（以 ISO 週一為一週起點） */
  function weeklyVolume() {
    var buckets = {};
    state.sessions.forEach(function (s) {
      if (typeof s.distance_km !== 'number' || !isFinite(s.distance_km)) return;
      var d = new Date(s.date + 'T00:00:00Z');
      var dow = d.getUTCDay();              // 0=週日
      var offset = dow === 0 ? 6 : dow - 1; // 回推到該週週一
      d.setUTCDate(d.getUTCDate() - offset);
      var key = d.toISOString().slice(0, 10);
      buckets[key] = (buckets[key] || 0) + s.distance_km;
    });
    return Object.keys(buckets).sort().map(function (k) {
      return { label: C.shortDate(k), value: Math.round(buckets[k] * 10) / 10, weekStart: k };
    });
  }

  function renderTrends() {
    var node = $('trend-charts');
    clear(node);
    if (state.sessions.length === 0) {
      node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步場次'));
      return;
    }

    var specs = [
      { title: '距離（km）', field: 'distance_km', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-distance', dotClass: 'dot-distance' },
      { title: '平均配速（/km，上快下慢）', field: 'avg_pace_sec_per_km', formatter: C.formatPace, invertY: true, lineClass: 'line-pace', dotClass: 'dot-pace' },
      { title: '平均心率（bpm）', field: 'avg_hr_bpm', formatter: function (v) { return C.formatNumber(v, 0); }, lineClass: 'line-hr', dotClass: 'dot-hr' },
      { title: '有氧訓練效果（TE）', field: 'aerobic_te', formatter: function (v) { return C.formatNumber(v, 1); }, lineClass: 'line-te', dotClass: 'dot-te' }
    ];

    specs.forEach(function (spec) {
      var card = h('div', 'chart-block');
      card.appendChild(h('h3', 'chart-title', spec.title));
      var series = seriesFromSessions(spec.field);
      var hasValue = series.some(function (p) { return p.value !== null; });
      if (!hasValue) {
        card.appendChild(h('div', 'state-msg state-unavailable', '此區間內此指標無資料'));
      } else {
        card.appendChild(wrapChart(C.trendLineChart(series, {
          label: spec.title,
          formatter: spec.formatter,
          invertY: spec.invertY,
          lineClass: spec.lineClass,
          dotClass: spec.dotClass,
          containerWidth: widthOf(node)
        })));
      }
      node.appendChild(card);
    });

    var volumeCard = h('div', 'chart-block');
    volumeCard.appendChild(h('h3', 'chart-title', '週跑量（km）'));
    var bars = weeklyVolume();
    if (bars.length === 0) {
      volumeCard.appendChild(h('div', 'state-msg state-unavailable', '此區間內無距離資料'));
    } else {
      volumeCard.appendChild(wrapChart(C.barChart(bars, {
        label: '週跑量長條圖',
        formatter: function (v) { return C.formatNumber(v, 1); },
        barClass: 'bar-volume',
        containerWidth: widthOf(node)
      })));
    }
    node.appendChild(volumeCard);
  }

  /**
   * 場次列表：桌機為表格，手機（<768px）由 CSS 改為卡片式堆疊，
   * 不使用橫向捲動的多欄表格。
   */
  function renderSessionList() {
    var node = $('session-list');
    clear(node);
    if (state.sessions.length === 0) return;

    node.appendChild(h('h3', 'chart-title', '場次列表'));
    var list = h('div', 'session-cards');
    state.sessions.forEach(function (s) {
      var row = h('button', 'session-card' + (s.id === state.selectedSessionId ? ' is-selected' : ''));
      row.type = 'button';
      row.setAttribute('aria-label', '檢視 ' + s.date + ' 的場次');

      var c1 = h('span', 'sc-cell sc-date');
      c1.appendChild(h('span', 'sc-key', '日期'));
      c1.appendChild(h('span', 'sc-val', s.date));

      var c2 = h('span', 'sc-cell');
      c2.appendChild(h('span', 'sc-key', '類型'));
      c2.appendChild(h('span', 'sc-val',
        s.workout_type ? (WORKOUT_TYPE_LABEL[s.workout_type] || s.workout_type) : '未標記'));

      var c3 = h('span', 'sc-cell');
      c3.appendChild(h('span', 'sc-key', '距離'));
      c3.appendChild(h('span', 'sc-val', C.formatNumber(s.distance_km, 2) + ' km'));

      var c4 = h('span', 'sc-cell');
      c4.appendChild(h('span', 'sc-key', '配速'));
      c4.appendChild(h('span', 'sc-val', C.formatPace(s.avg_pace_sec_per_km) + '/km'));

      var c5 = h('span', 'sc-cell');
      c5.appendChild(h('span', 'sc-key', '平均心率'));
      c5.appendChild(h('span', 'sc-val',
        s.avg_hr_bpm === null || s.avg_hr_bpm === undefined ? '—' : s.avg_hr_bpm + ' bpm'));

      var c6 = h('span', 'sc-cell');
      c6.appendChild(h('span', 'sc-key', '時間'));
      c6.appendChild(h('span', 'sc-val', C.formatDuration(s.duration_sec)));

      [c1, c2, c3, c4, c5, c6].forEach(function (c) { row.appendChild(c); });
      row.addEventListener('click', function () { selectSession(s.id); });
      list.appendChild(row);
    });
    node.appendChild(list);
  }

  // ------------------------------------------------------------ 區塊三：每日身體狀況

  /** 依 def.format 選對應的格式化函式（'duration' 用時分秒，其餘用一般數字）。 */
  function wellnessFormatter(def) {
    if (def.format === 'duration') return function (v) { return C.formatDuration(v); };
    return function (v) { return C.formatNumber(v, def.decimals); };
  }

  /**
   * @param {boolean} [keepPanelOpen] 重繪後是否自動重新展開自訂面板——
   *   排序/顯隱操作都會呼叫 renderWellness() 整批重繪（含面板容器本身
   *   一併清掉重建），若每次操作都要使用者手動重新點開面板，連續調整
   *   多個指標的順序會非常繁瑣，故排序/勾選操作一律傳 true。
   */
  function renderWellness(keepPanelOpen) {
    var node = $('wellness-charts');
    clear(node);
    if (!state.wellness) return;

    var customizeWrap = wellnessCustomizeButton();
    node.appendChild(customizeWrap);
    if (keepPanelOpen) customizeWrap.appendChild(buildWellnessCustomizePanel());

    var metrics = state.wellness.metrics || {};
    var startDate = state.wellness.start_date;
    var endDate = state.wellness.end_date;

    orderedVisibleWellnessDefs().forEach(function (def) {
      var m = metrics[def.key];
      var formatter = wellnessFormatter(def);
      var card = h('div', 'chart-block');
      var titleRow = h('div', 'chart-title-row');
      titleRow.appendChild(h('h3', 'chart-title', def.label + (def.unit ? '（' + def.unit + '）' : '')));

      if (!m) {
        card.appendChild(titleRow);
        card.appendChild(h('div', 'state-msg state-unavailable', '後端未提供此指標'));
        node.appendChild(card);
        return;
      }

      // clipped:true → 明確提示，且絕不把缺的部分畫成 0 或平線
      if (m.clipped) {
        var cov = m.coverage && m.coverage.earliest_date ? m.coverage.earliest_date : null;
        titleRow.appendChild(h('span', 'badge badge-clipped',
          cov ? (cov + ' 前無資料') : '此日期前無資料'));
      }
      card.appendChild(titleRow);

      var points = (m.points || []).filter(function (p) {
        return typeof p.value === 'number' && isFinite(p.value);
      });

      if (m.available === false || points.length === 0) {
        card.appendChild(h('div', 'state-msg state-unavailable',
          m.reason || '此區間內無資料'));
        node.appendChild(card);
        return;
      }

      // 圖表 X 軸起點：clipped 時從實際有資料的第一天畫起，避免產生誤導的空白平線
      var chartStart = startDate;
      if (m.clipped && m.coverage && m.coverage.earliest_date && m.coverage.earliest_date > startDate) {
        chartStart = m.coverage.earliest_date;
      }
      if (points[0].date > chartStart) chartStart = points[0].date;

      card.appendChild(wrapChart(C.wellnessChart(points, {
        label: def.label + '折線圖',
        startDate: chartStart,
        endDate: endDate,
        trainingDays: state.trainingDays,
        formatter: formatter,
        lineClass: 'line-metric-' + def.key,
        dotClass: 'dot-metric',
        containerWidth: widthOf(node)
      })));

      var latest = points[points.length - 1];
      card.appendChild(h('div', 'chart-note',
        '最新（' + latest.date + '）：' + formatter(latest.value)
        + (def.unit && def.format !== 'duration' ? ' ' + def.unit : '') + '　・　共 ' + points.length + ' 天有資料'));
      node.appendChild(card);
    });
  }

  /**
   * 「⚙ 自訂」按鈕：展開一個可排序／可勾選顯示的指標清單面板。
   * 排序用上下箭頭而非拖曳——HTML5 drag&drop 在行動瀏覽器完全無效
   * （觸控不會觸發 drag 事件），自製觸控拖曳成本遠超單人自用工具的需求；
   * 上下箭頭鍵盤可操作、觸控可點擊（≥44px），且資料模型（有序 key 陣列）
   * 與拖曳互動層完全獨立，日後真要換成拖曳也不需要動這裡的儲存格式。
   */
  function wellnessCustomizeButton() {
    var wrap = h('div', 'wellness-customize');
    var btn = h('button', 'wellness-customize-toggle', '⚙ 自訂');
    btn.type = 'button';
    btn.addEventListener('click', function () { toggleWellnessCustomizePanel(wrap); });
    wrap.appendChild(btn);
    return wrap;
  }

  function toggleWellnessCustomizePanel(wrap) {
    var existing = wrap.querySelector('.wellness-customize-panel');
    if (existing) { existing.parentNode.removeChild(existing); return; }
    wrap.appendChild(buildWellnessCustomizePanel());
  }

  function buildWellnessCustomizePanel() {
    var panel = h('div', 'wellness-customize-panel');
    var defs = wellnessMetricDefs();
    var visible = orderedVisibleWellnessDefs();
    var visibleKeys = visible.map(function (d) { return d.key; });
    // 面板要能勾選「目前隱藏」的指標重新顯示，所以完整順序 = 目前可見的
    // （依使用者順序）+ 目前隱藏的（依 defs 原始順序，排在後面）
    var hiddenDefs = defs.filter(function (d) { return visibleKeys.indexOf(d.key) === -1; });
    var fullOrder = visible.concat(hiddenDefs);

    function currentHiddenSet() {
      var hidden = {};
      hiddenDefs.forEach(function (d) { hidden[d.key] = true; });
      return hidden;
    }

    function persistAndRerender(order, hiddenSet) {
      var hiddenList = order.filter(function (k) { return hiddenSet[k]; });
      saveWellnessSettings(order, hiddenList);
      renderWellness(true); // 保持面板開啟，讓連續排序/勾選操作不必每次重新點開
    }

    var list = h('div', 'wellness-customize-list');
    var hiddenSet = currentHiddenSet();

    fullOrder.forEach(function (def, idx) {
      var row = h('div', 'wellness-customize-row');

      var checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !hiddenSet[def.key];
      checkbox.addEventListener('change', function () {
        var order = fullOrder.map(function (d) { return d.key; });
        if (checkbox.checked) delete hiddenSet[def.key]; else hiddenSet[def.key] = true;
        persistAndRerender(order, hiddenSet);
      });
      row.appendChild(checkbox);
      row.appendChild(h('span', 'wellness-customize-label', def.label));

      var upBtn = h('button', 'wellness-customize-move', '↑');
      upBtn.type = 'button';
      upBtn.disabled = idx === 0;
      upBtn.addEventListener('click', function () {
        var order = fullOrder.map(function (d) { return d.key; });
        var tmp = order[idx - 1]; order[idx - 1] = order[idx]; order[idx] = tmp;
        persistAndRerender(order, hiddenSet);
      });
      var downBtn = h('button', 'wellness-customize-move', '↓');
      downBtn.type = 'button';
      downBtn.disabled = idx === fullOrder.length - 1;
      downBtn.addEventListener('click', function () {
        var order = fullOrder.map(function (d) { return d.key; });
        var tmp = order[idx + 1]; order[idx + 1] = order[idx]; order[idx] = tmp;
        persistAndRerender(order, hiddenSet);
      });
      row.appendChild(upBtn);
      row.appendChild(downBtn);

      list.appendChild(row);
    });

    panel.appendChild(list);
    return panel;
  }

  // ------------------------------------------------------------ 總覽
  //
  // 回答「最近狀態怎樣」的跨週數據頁（2026-10-14 依 Fu 的參考圖改版）：
  //   摘要列（區間摘要／資料品質／資料新鮮度）→ 5 張指標卡 → 週跑量＋心率區間並排 → 最佳成績
  // 資料來自兩支端點：
  //   /api/overview       除 ATL/CTL/TSB 以外的全部
  //   /api/training-load  ATL/CTL/TSB（與訓練負荷頁共用 state.loadSeries，不另抓）
  // 每張卡標出時間範圍（window-chip），ⓘ 只寫定義與算法，不寫判讀（判讀屬 AI 教練面板）。
  // 增減幅度等標籤一律用中性樣式，不用紅綠暗示好壞。

  // 強度分層的顯示資訊。分層本身（哪些 zone 算低／中／高）以後端回傳的
  // weekly_intensity.tiers 為準，這裡只決定顏色與名稱。
  var INTENSITY_TIERS = [
    { key: 'low', name: '低強度', className: 'bar-intensity-low' },
    { key: 'mid', name: '中強度', className: 'bar-intensity-mid' },
    { key: 'high', name: '高強度', className: 'bar-intensity-high' },
    { key: 'unknown', name: '未分類', className: 'bar-intensity-unknown' }
  ];

  // 最佳成績卡每個距離預設顯示幾場（第 1 名為最佳，其餘為次佳）

  var OVERVIEW_NODE_IDS = ['overview-strip', 'overview-kpis', 'overview-volume', 'overview-zones', 'overview-plan', 'overview-pbs'];

  function zoneListText(zones) {
    return zones.map(function (z) { return z === 0 ? '低於 Z1' : 'Z' + z; }).join(' + ');
  }

  /** 帶時間範圍標籤與 ⓘ 的面板標題列。 */
  function overviewHead(title, chip, tipText) {
    var head = h('div', 'overview-head');
    var t = h('h3', 'overview-title', title);
    if (chip) t.appendChild(h('span', 'window-chip', chip));
    head.appendChild(t);
    head.appendChild(infoTip(title, tipText));
    return head;
  }

  /** 百分比變化的中性標籤：+12% / −38%（前值為 0 時不算百分比，只標「前期 0」）。 */
  function changeLabel(current, previous, suffix) {
    if (!previous) return '前期 0 km';
    var pct = (current - previous) / previous * 100;
    return (pct >= 0 ? '+' : '−') + Math.abs(pct).toFixed(0) + '% ' + suffix;
  }

  /** range 系列卡片的時間標籤：實際起訖日（range=all 時沒有起日）。 */
  function rangeChip(ov) {
    return ov.start_date
      ? C.shortDate(ov.start_date) + '–' + C.shortDate(ov.end_date)
      : '全部（至 ' + C.shortDate(ov.end_date) + '）';
  }

  /** 'YYYY-MM-DDTHH:MM…' → 'M/D HH:MM'。帶 Z 的 UTC 時間會換算成瀏覽器當地時間。 */
  function shortDateTime(iso) {
    if (!iso) return '—';
    if (/Z$/.test(iso)) {
      var d = new Date(iso);
      return (d.getMonth() + 1) + '/' + d.getDate() + ' '
        + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    }
    return C.shortDate(iso.slice(0, 10)) + ' ' + iso.slice(11, 16);
  }

  function avgFullWeeks(weeks) {
    var full = weeks.filter(function (w) { return !w.partial; });
    if (full.length === 0) return null;
    return full.reduce(function (s, w) { return s + w.total_km; }, 0) / full.length;
  }

  // ---- 摘要列

  function renderOverviewStrip(ov) {
    var node = $('overview-strip');
    clear(node);
    var s = ov.sessions;
    var c = s.criteria;
    var weekAvg = avgFullWeeks(ov.weekly_intensity.weeks);
    var strip = h('div', 'overview-strip');

    function cell(title, chip, parts, tipText) {
      var box = h('div', 'strip-cell');
      var head = h('div', 'strip-title', title);
      if (chip) head.appendChild(h('span', 'window-chip', chip));
      if (tipText) head.appendChild(infoTip(title, tipText));
      box.appendChild(head);
      var line = h('div', 'strip-line');
      parts.forEach(function (p, i) {
        if (i > 0) line.appendChild(document.createTextNode(' · '));
        if (Array.isArray(p)) {
          line.appendChild(document.createTextNode(p[0]));
          line.appendChild(h('strong', null, p[1]));
          if (p[2]) line.appendChild(document.createTextNode(p[2]));
        } else {
          line.appendChild(document.createTextNode(p));
        }
      });
      box.appendChild(line);
      return box;
    }

    var summary = cell('摘要', rangeChip(ov), [
      ['跑步 ', String(s.total), ' 場'],
      ['', C.formatNumber(s.total_km, 1), ' km'],
      ['', C.formatDuration(s.total_sec)],
      weekAvg === null ? '無完整週' : ['完整週平均 ', C.formatNumber(weekAvg, 1), ' km']
    ]);
    summary.classList.add('strip-summary');
    summary.appendChild(aiAnalysisRow());
    strip.appendChild(summary);
    strip.appendChild(cell('資料品質', rangeChip(ov), [
      ['有效訓練 ', s.effective + '/' + s.total],
      ['心率區間 ', (ov.hr_zones.available ? ov.hr_zones.sessions_counted : 0) + '/' + s.total, ' 場']
    ], [
      '有效訓練：時間 ≥ ' + Math.round(c.min_duration_sec / 60) + ' 分鐘、距離 ≥ ' + c.min_distance_km + ' km'
        + (c.requires_hr ? '、且有心率資料' : '') + '的跑步。用來排除手錶誤觸、起跑就停、沒戴心率的紀錄；是資料品質篩選，不代表練得好不好。',
      '心率區間：有手錶心率區間停留時間的場次，下方強度堆疊與區間分佈只算得到這些場次。'
    ]));
    node.appendChild(strip);
  }

  /**
   * 「AI 分析最近訓練與身體狀況」按鈕：打開右側 AI 面板的分析報告畫面，由後端
   * 在背景啟動 Claude Code 無互動 session 查資料、寫分析（不需要 API key），
   * 過程與報告串流顯示在面板裡並自動保存。分析問題的原文在後端
   * （routes_analysis.ANALYSIS_PROMPT），這裡不帶任何 prompt。
   * 分析刻意不顯示在總覽頁上（D.1：解讀只出現在 AI 面板，2026-10-14 Fu 確認）。
   */
  function aiAnalysisRow() {
    var row = h('div', 'ai-analysis-row');
    var btn = h('button', 'ai-analysis-btn');
    btn.type = 'button';
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + '<use href="#icon-bubble"></use></svg>';
    btn.appendChild(document.createTextNode('AI 分析最近訓練與身體狀況'));
    var hint = h('span', 'ai-analysis-hint', '約 1～2 分鐘，結果顯示在右側面板並自動保存');
    var history = h('button', 'ai-analysis-history', '查看過去的分析');
    history.type = 'button';

    function panelReady() {
      if (window.CoachPanel && window.CoachPanel.runAnalysis) return true;
      hint.textContent = 'AI 面板尚未載入';
      return false;
    }
    btn.addEventListener('click', function () {
      if (!panelReady()) return;
      btn.disabled = true;
      hint.textContent = '分析中…（右側面板可看進度）';
      window.CoachPanel.runAnalysis().then(function () {
        hint.textContent = '分析完成，已保存';
      }, function (err) {
        hint.textContent = err && err.message ? err.message : '無法開始分析';
      }).then(function () { btn.disabled = false; });
    });
    history.addEventListener('click', function () {
      if (panelReady()) window.CoachPanel.showAnalyses();
    });
    row.appendChild(btn);
    row.appendChild(history);
    row.appendChild(hint);
    return row;
  }

  // ---- 指標卡

  function vdotCard(vdot) {
    var race = vdot.race || {};
    var ref = vdot.garmin_reference;
    var refText = null;
    if (ref && ref.predictions.length > 0) {
      var vals = ref.predictions.map(function (p) { return p.vdot; });
      var lo = Math.min.apply(null, vals);
      var hi = Math.max.apply(null, vals);
      refText = 'Garmin 對照 ' + C.formatNumber(lo, 1) + (hi !== lo ? '–' + C.formatNumber(hi, 1) : '');
    }
    var tip = [
      'Jack Daniels 的跑力指數：用一場全力成績反推的有氧能力分數。',
      '只採用標記為比賽或全力測驗的成績（3K／5K／10K 需在 90 天內，半馬／全馬 180 天內），'
        + '把實際距離與完賽時間代入 Daniels & Gilbert 公式：VDOT ＝ 該配速的攝氧成本 ÷ 能持續該時間的最大攝氧比例。'
        + '有手動計時成績的場次用手動計時成績。',
      '走勢線是歷年每場比賽各自算出的 VDOT（不受新鮮度門檻限制）。',
      '不採用一般訓練經心率換算的推估：那是次最大強度推估，對訓練量少的跑者會偏高。'
    ];
    if (ref) {
      tip.push('Garmin 對照值：把手錶 ' + ref.as_of + ' 的完賽預測（'
        + ref.predictions.map(function (p) {
          return p.distance + ' ' + C.formatDuration(p.time_sec) + ' → ' + C.formatNumber(p.vdot, 1);
        }).join('、')
        + '）用同一公式換算。Garmin 演算法未公開，僅供對照，不作為依據。');
    }
    var history = (vdot.history || []).map(function (x) { return x.vdot; });
    if (!race.value) {
      return kpiCard({
        label: 'VDOT', chip: '最近全力成績', value: '未評',
        badge: h('span', 'conf-badge conf-none', '無資料'),
        note: race.reason + (refText ? '；' + refText : ''),
        series: history.length > 1 ? history : null,
        tip: tip
      });
    }
    return kpiCard({
      label: 'VDOT',
      chip: '最近全力成績',
      value: C.formatNumber(race.value.value, 1),
      badge: confBadge(race.value),
      note: C.shortDate(race.basis.date) + ' ' + race.basis.distance + (refText ? '　' + refText : ''),
      series: history.length > 1 ? history : null,
      tip: tip
    });
  }

  function thisWeekCard(w) {
    var card = kpiCard({
      label: '本週跑量',
      chip: C.shortDate(w.week_start) + '–' + C.shortDate(w.week_end),
      value: C.formatNumber(w.done_km, 1),
      unit: w.planned_km !== null ? '/ ' + C.formatNumber(w.planned_km, 1) + ' km' : 'km',
      note: w.planned_km === null
        ? '本週無課表'
        : (w.planned_without_distance > 0 ? '課表另 ' + w.planned_without_distance + ' 堂只給時間' : '課表距離'),
      tip: [
        '本週（週一至週日，以今天為準，不受上方時間區間影響）已完成的跑步距離，斜線後為課表距離。',
        '課表距離只加總有寫距離的課；只寫時間的課（例如 FR 60 分鐘）無法換算距離，另外註明堂數，不拿配速去猜。'
      ]
    });
    if (w.planned_km) {
      var pct = Math.min(100, (w.done_km / w.planned_km) * 100);
      var bar = h('div', 'kpi-bar');
      var fill = h('div', 'kpi-bar-fill');
      fill.style.width = pct.toFixed(1) + '%';
      bar.appendChild(fill);
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', '本週跑量佔課表距離 ' + Math.round(pct) + '%');
      card.insertBefore(bar, card.querySelector('.kpi-note'));
    }
    return card;
  }

  function recentVolumeCard(r, weeks) {
    // 走勢線：區間內每週總跑量（最後一週可能未完整，仍畫出來讓「現在」看得見）
    var series = weeks.map(function (w) { return w.total_km; });
    return kpiCard({
      label: '近 ' + (r.days / 7) + ' 週跑量',
      chip: C.shortDate(r.start) + '–' + C.shortDate(r.end),
      value: C.formatNumber(r.km, 1),
      unit: 'km',
      badge: h('span', 'neutral-badge', changeLabel(r.km, r.previous_km, 'vs 前 ' + (r.days / 7) + ' 週')),
      note: '前 ' + (r.days / 7) + ' 週 ' + C.formatNumber(r.previous_km, 1) + ' km（'
        + C.shortDate(r.previous_start) + '–' + C.shortDate(r.previous_end) + '）',
      series: series.length > 1 ? series : null,
      tip: [
        '最近 ' + r.days + ' 天（含今天）的跑步距離，與再往前 ' + r.days + ' 天對照。以今天為準滾動計算，不受上方時間區間影響。',
        '走勢線是上方時間區間內的每週跑量。'
      ]
    });
  }

  function zone2Card(w, bounds) {
    var secs = {};
    w.zone_seconds.forEach(function (z) { secs[z.zone] = z.seconds; });
    var total = w.zone_seconds.reduce(function (s, z) { return s + z.seconds; }, 0);
    var z2 = bounds.zones[1];
    var range = bounds.available ? '（' + z2.bpm_low + '–' + z2.bpm_high + ' bpm）' : '';
    var card = kpiCard({
      label: '本週 Zone 2 時間',
      chip: C.shortDate(w.week_start) + '–' + C.shortDate(w.week_end),
      value: total > 0 ? C.formatDuration(secs[2] || 0) : '—',
      badge: total > 0 ? h('span', 'neutral-badge', '佔本週 ' + C.formatNumber((secs[2] || 0) / total * 100, 0) + '%') : null,
      note: total > 0
        ? '本週心率區間共 ' + C.formatDuration(total) + '，' + w.zone_sessions_counted + '/' + w.sessions + ' 場有資料'
        : (w.sessions > 0 ? '本週跑步皆無心率區間資料' : '本週尚未跑步'),
      tip: [
        '本週（週一至週日）跑步在 Z2 輕鬆（Easy）' + range + '的停留時間加總，來自手錶每場記錄的區間時間。',
        '百分比的分母是本週跑步在 Z1～Z5 的總時間。'
      ]
    });
    if (total > 0) card.insertBefore(zoneSplitBar(w.zone_seconds.map(function (z) {
      return { zone: z.zone, pct: z.seconds / total * 100 };
    }), true), card.querySelector('.kpi-note'));
    return card;
  }

  /** 訓練負荷卡：主數字 TSB，附「佔 CTL 的百分比」讓自訂尺度的數字可讀。 */
  function loadCard() {
    var rows = (state.loadSeries && state.loadSeries.series) || [];
    var last = null;
    for (var i = rows.length - 1; i >= 0; i -= 1) {
      if (rows[i].tsb && typeof rows[i].tsb.value === 'number') { last = rows[i]; break; }
    }
    var tip = [
      'TSB（訓練壓力平衡）＝ CTL − ATL。ATL 是每日訓練負荷的 7 日指數加權平均（近期疲勞），CTL 是 42 日指數加權平均（長期體能基礎）。',
      '負荷 ＝ 運動時間 × 心率儲備百分比（%HRR），是本專案自建尺度，數字大小沒有直覺意義，'
        + '所以另外標出 TSB 佔 CTL 的百分比：表示近期負荷比長期基礎高（負）或低（正）多少。',
      '參考值（Joe Friel，TrainingPeaks〈Applying the Numbers Part 3〉，原為 TSS 尺度的 TSB）：'
        + '比賽日 +15～+25 通常表現最好（部分選手 +5～+10）；−10～+10 是過渡區，停留時間應短；'
        + '負值代表處於疲勞、多半不在比賽狀態。',
      '本專案負荷尺度與 TSS 不同（約差 30 倍），上面的絕對值不能直接套在 TSB 上。'
        + 'intervals.icu 的做法是把同一組數字改套在「TSB 佔 CTL 的百分比」，使用者社群常引用 −30% 以下為高風險區'
        + '——這是社群慣例，不是官方文件或研究定論。以上僅供對照，本頁不據此判讀。'
    ];
    if (!last) {
      return kpiCard({
        label: '訓練負荷', value: '—', tip: tip,
        note: state.loadSeries ? '此區間內無訓練負荷資料' : '載入中…'
      });
    }
    var tsb = last.tsb.value;
    var ctl = last.ctl && last.ctl.value;
    return kpiCard({
      label: '訓練負荷',
      chip: '截至 ' + C.shortDate(last.date),
      value: (tsb > 0 ? '+' : '') + C.formatNumber(tsb, 0),
      unit: 'TSB',
      badge: ctl ? h('span', 'neutral-badge', 'CTL 的 ' + (tsb > 0 ? '+' : '−') + Math.abs(tsb / ctl * 100).toFixed(0) + '%') : null,
      note: 'ATL ' + C.formatNumber(last.atl && last.atl.value, 0) + ' · CTL ' + C.formatNumber(ctl, 0),
      series: rows.map(function (r) { return r.tsb ? r.tsb.value : null; }),
      tip: tip
    });
  }

  function renderOverviewKpis(ov) {
    var node = $('overview-kpis');
    clear(node);
    var strip = h('div', 'kpi-strip overview-kpi-strip');
    strip.appendChild(vdotCard(ov.vdot));
    strip.appendChild(thisWeekCard(ov.this_week));
    strip.appendChild(recentVolumeCard(ov.recent_volume, ov.weekly_intensity.weeks));
    strip.appendChild(zone2Card(ov.this_week, ov.hr_zone_bounds));
    var load = loadCard();
    // 信賴度 badge 與「佔 CTL」標籤同列會擠，信賴度改放在 ATL/CTL 那行
    var lastTsb = null;
    ((state.loadSeries && state.loadSeries.series) || []).forEach(function (r) { if (r.tsb) lastTsb = r.tsb; });
    var note = load.querySelector('.kpi-note');
    if (lastTsb && note) note.appendChild(confBadge(lastTsb));
    strip.appendChild(load);
    node.appendChild(strip);
  }

  // ---- 週跑量（強度堆疊）

  function renderOverviewVolume(ov) {
    var node = $('overview-volume');
    clear(node);
    var wi = ov.weekly_intensity;
    var tiers = wi.tiers || {};
    var head = overviewHead('每週跑量（依強度堆疊）', rangeChip(ov) + ' · 週一起算', [
      '每週的跑步距離，依每場心率區間停留時間的比例拆分：'
        + INTENSITY_TIERS.filter(function (t) { return tiers[t.key]; }).map(function (t) {
          return t.name + ' = ' + zoneListText(tiers[t.key]);
        }).join('；') + '。',
      '區間是手錶上設定的 Garmin %HRR 五區。沒有心率區間資料的場次整場列為「未分類」，不猜強度。',
      '淡色長條是被時間區間切到、或還沒過完的週；虛線是本週課表距離。'
    ]);
    node.appendChild(head);

    if (wi.weeks.length === 0) {
      node.appendChild(h('div', 'state-msg state-unavailable', '此區間內沒有跑步'));
      return;
    }
    var thisWeekIdx = -1;
    var bars = wi.weeks.map(function (wk, i) {
      if (wk.week_start === ov.this_week.week_start) thisWeekIdx = i;
      return {
        label: C.shortDate(wk.week_start),
        weekStart: wk.week_start,
        partial: wk.partial,
        tipSuffix: wk.partial ? '（不完整週）' : '',
        segments: INTENSITY_TIERS.map(function (t) {
          return { value: wk[t.key + '_km'], className: t.className, name: t.name };
        })
      };
    });
    var targets = [];
    if (thisWeekIdx >= 0 && ov.this_week.planned_km) {
      targets.push({ index: thisWeekIdx, value: ov.this_week.planned_km,
                     label: '課表 ' + C.formatNumber(ov.this_week.planned_km, 1) });
    }
    node.appendChild(wrapChart(C.barChart(bars, {
      label: '每週跑量依強度堆疊長條圖',
      formatter: function (v) { return C.formatNumber(v, 1); },
      containerWidth: widthOf(node),
      targets: targets
    })));
    node.appendChild(intensityLegend());
  }

  function intensityLegend() {
    var legend = h('div', 'legend overview-legend');
    INTENSITY_TIERS.forEach(function (t) {
      var item = h('span', 'legend-item');
      item.appendChild(h('span', 'swatch swatch-' + t.className));
      item.appendChild(h('span', null, t.name));
      legend.appendChild(item);
    });
    return legend;
  }

  // ---- 心率區間

  /**
   * 低／中／高三段比例條，80% 處畫參考線（80/20 只是參考位置，不是判讀）。
   * @param {Array} zones [{zone, pct}]，pct 分母為 Z1~Z5
   * @param {boolean} compact 指標卡內用的細版
   */
  function zoneSplitBar(zones, compact) {
    var pct = {};
    zones.forEach(function (z) { pct[z.zone] = z.pct || 0; });
    var parts = [
      { cls: 'bar-intensity-low', v: (pct[1] || 0) + (pct[2] || 0) },
      { cls: 'bar-intensity-mid', v: pct[3] || 0 },
      { cls: 'bar-intensity-high', v: (pct[4] || 0) + (pct[5] || 0) }
    ];
    var bar = h('div', 'zone-split' + (compact ? ' is-compact' : ''));
    parts.forEach(function (p) {
      var seg = h('span', 'zone-split-seg ' + p.cls);
      seg.style.width = p.v.toFixed(2) + '%';
      bar.appendChild(seg);
    });
    bar.appendChild(h('span', 'zone-split-ref'));
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', '低強度 ' + parts[0].v.toFixed(1) + '%，中強度 '
      + parts[1].v.toFixed(1) + '%，高強度 ' + parts[2].v.toFixed(1) + '%');
    return bar;
  }

  function renderOverviewZones(ov) {
    var node = $('overview-zones');
    clear(node);
    var zones = ov.hr_zones;
    var bounds = ov.hr_zone_bounds;
    var tipText = [
      '上方時間區間（目前 ' + rangeChip(ov) + '）內所有跑步在 Z1～Z5 的停留時間加總，來自手錶每場記錄的區間時間。'
        + '佔比的分母只含 Z1～Z5，不含低於 Z1 的暖身與停等時間。',
      '上方比例條：低強度 Z1–Z2、中強度 Z3、高強度 Z4–Z5；黑線標在 80% 的位置，對照 80/20 原則。'
    ];
    if (bounds.available) {
      tipText.push('bpm 範圍依手錶 %HRR 區間設定換算：靜止心率 ' + bounds.resting_hr_bpm
        + ' ＋ %HRR ×（最大心率 ' + bounds.max_hr.value + ' − ' + bounds.resting_hr_bpm
        + '）。最大心率為手錶顯示值，若比實際高，所有區間都會跟著偏高。');
    }
    var head = overviewHead('心率區間分佈',
      rangeChip(ov) + (zones.available ? ' · ' + zones.sessions_counted + ' 場' : ''), tipText);
    node.appendChild(head);

    if (!zones.available) {
      node.appendChild(h('div', 'state-msg state-unavailable', zones.reason));
      return;
    }
    node.appendChild(zoneSplitBar(zones.zones, false));
    var pct = {};
    zones.zones.forEach(function (z) { pct[z.zone] = z; });
    var low = (pct[1].pct || 0) + (pct[2].pct || 0);
    var high = (pct[4].pct || 0) + (pct[5].pct || 0);
    node.appendChild(h('div', 'zone-split-labels',
      '低 ' + C.formatNumber(low, 1) + '%　中 ' + C.formatNumber(pct[3].pct, 1) + '%　高 ' + C.formatNumber(high, 1) + '%'));

    var list = h('div', 'zone-rows');
    bounds.zones.forEach(function (b) {
      var z = pct[b.zone] || { seconds: 0, pct: 0 };
      var row = h('div', 'zone-row');
      var name = h('span', 'zone-name');
      name.appendChild(h('strong', null, 'Z' + b.zone + ' ' + b.name));
      name.appendChild(h('span', 'zone-name-en', b.name_en));
      row.appendChild(name);
      row.appendChild(h('span', 'zone-bpm', bounds.available ? b.bpm_low + '–' + b.bpm_high : ''));
      var track = h('span', 'zone-track');
      var fill = h('span', 'zone-fill bar-zone-fill-' + b.zone);
      fill.style.width = (z.pct || 0) + '%';
      track.appendChild(fill);
      row.appendChild(track);
      var val = h('span', 'zone-pct', C.formatNumber(z.pct, 1) + '%');
      val.title = C.formatDuration(z.seconds);
      row.appendChild(val);
      list.appendChild(row);
    });
    node.appendChild(list);
    node.appendChild(h('p', 'chart-note', '共 ' + C.formatDuration(zones.total_seconds)
      + (bounds.available ? '　bpm 依手錶 %HRR 設定換算（最大 ' + bounds.max_hr.value + '、靜止 ' + bounds.resting_hr_bpm + '）' : '　尚未設定手錶區間，不顯示 bpm')));
  }

  // ---- 本週課表

  /**
   * 教練原文的精簡版：去掉開頭的「D2：」編號，並在第一個全形括號（我們寫入時
   * 附的換算說明）之前截斷。完整原文放在 title，hover／長按看得到。
   */
  function shortPlanNote(notes) {
    if (!notes) return '';
    var text = notes.replace(/^D\d+[：:]\s*/, '');
    var cut = text.indexOf('（');
    return cut > 0 ? text.slice(0, cut) : text;
  }

  function planSummary(p) {
    var parts = [WORKOUT_TYPE_LABEL[p.workout_type] || p.workout_type];
    if (p.planned_distance_km) parts.push(C.formatNumber(p.planned_distance_km, 1) + ' km');
    else if (p.planned_duration_sec) parts.push(Math.round(p.planned_duration_sec / 60) + ' 分');
    if (p.planned_pace_sec_per_km) parts.push('@' + C.formatPace(p.planned_pace_sec_per_km));
    return parts.join(' ');
  }

  function renderOverviewPlan() {
    var node = $('overview-plan');
    if (!node || !state.overviewWeek) return;
    clear(node);
    var days = state.overviewWeek;
    var today = todayISO();
    var hasPlan = days.some(function (d) { return d.planned.length > 0; });
    // 課表來源寫在標題旁一次就好（逐日重複標示只是雜訊）
    var sources = {};
    days.forEach(function (d) { d.planned.forEach(function (p) { sources[p.plan_source] = true; }); });
    var sourceText = [sources.external ? '跑班課表' : null, sources.generated ? '系統課表' : null]
      .filter(Boolean).join('＋');
    var head = overviewHead('本週課表',
      C.shortDate(days[0].date) + '–' + C.shortDate(days[days.length - 1].date)
        + (sourceText ? ' · ' + sourceText : ''), [
        '本週（週一至週日）每天的課表與實際跑步。標題旁標示課表來源：跑班課表＝教練給的外部課表，系統課表＝系統自動產生。'
          + '每天第二行是教練原文（hover 可看完整內容）。',
        '實際一欄是當天的跑步距離與平均配速，點一下可看該場的單場分析。'
          + '課表與活動是用「同一天＋運動類型相符」推論配對的，當天若臨時改課，配對可能不對。',
        '狀態只描述有沒有對應的活動（完成／未執行／今天待完成／額外訓練），不評斷練得好不好。'
      ]);
    node.appendChild(head);
    if (!hasPlan) {
      node.appendChild(h('div', 'state-msg state-unavailable', '本週沒有課表'));
    }

    var grid = h('div', 'plan-week');
    days.forEach(function (day) {
      var cell = h('div', 'plan-day day-' + day.day_state + (day.date === today ? ' is-today' : ''));
      var top = h('div', 'plan-day-top');
      top.appendChild(h('span', 'plan-dow', '週' + dowLabel(day.date)));
      top.appendChild(h('span', 'plan-date', C.shortDate(day.date)));
      cell.appendChild(top);

      if (day.planned.length === 0) {
        cell.appendChild(h('div', 'plan-type plan-rest', '—'));
      }
      day.planned.forEach(function (p) {
        cell.appendChild(h('div', 'plan-type' + (p.workout_type === 'rest' ? ' plan-rest' : ''), planSummary(p)));
        var note = shortPlanNote(p.notes);
        if (note && p.workout_type !== 'rest') {
          var n = h('div', 'plan-note', note);
          n.title = p.notes;
          cell.appendChild(n);
        }
      });

      var runs = day.activities.filter(function (a) { return a.is_running; });
      runs.forEach(function (a) {
        var btn = h('button', 'plan-actual');
        btn.type = 'button';
        var pace = a.distance_km ? a.duration_sec / a.distance_km : null;
        btn.textContent = '實際 ' + C.formatNumber(a.distance_km, 1) + ' km'
          + (pace ? ' · ' + C.formatPace(pace) + '/km' : '');
        btn.setAttribute('aria-label', day.date + ' 實際跑步 ' + C.formatNumber(a.distance_km, 1) + ' 公里，檢視單場分析');
        btn.addEventListener('click', function () { navigate('sessions', a.id); });
        cell.appendChild(btn);
      });

      if (day.day_state !== 'rest_ok' && day.day_state !== 'future') {
        cell.appendChild(h('span', 'plan-state', DAY_STATE_LABEL[day.day_state] || day.day_state));
      }
      grid.appendChild(cell);
    });
    node.appendChild(grid);
  }

  /** 本週課表用的 ISO 週（週一～週日）日曆資料，錨定真實今天、與頂列區間無關。 */
  function loadOverviewWeek() {
    var start = mondayOf(todayISO()).toISOString().slice(0, 10);
    var end = addDaysISO(start, 6);
    api('/api/calendar?start=' + start + '&end=' + end).then(function (data) {
      state.overviewWeek = data.days || [];
      renderOrDefer('overview', renderOverview);
    }).catch(function (err) {
      showError($('overview-plan'), err.message);
    });
  }

  // ---- 最佳成績

  // 展開中的距離（distance 代碼）；重繪總覽時保留，切換頁面再回來也還在
  var pbOpenDistance = null;

  /** 卡片上的賽事名稱：個人測驗一律顯示「個人測驗」，完整名稱留給展開清單 */
  function pbCardName(r) {
    return r.personal_test ? '個人測驗' : (r.name || '');
  }

  function renderOverviewPbs(ov) {
    var node = $('overview-pbs');
    clear(node);
    var head = overviewHead('個人最佳成績', '生涯', [
      '標記為 Race 的場次（含個人計時測驗），每個距離只顯示最佳一筆；點卡片可展開該距離的全部成績，由快到慢排列，點清單中任一筆可看單場分析。不受上方時間區間影響。',
      '成績預設用手錶紀錄的時間；有特別填入時間的場次（大會成績或現場計時）改用填入值，展開清單中標「手動」。配速一律為「時間 ÷ 標準距離」，不用手錶 GPS 量到的距離。',
      '賽事名稱讀自學員檔 athlete_profile.md 的「比賽成績」表（以日期對應），沒寫的場次用 Garmin 活動名稱；名稱含「測驗」或「測試」的在卡片上顯示為「個人測驗」。每場的 VDOT 用同一條 Daniels 公式算出。'
    ]);
    node.appendChild(head);

    var byDistance = {};
    ov.personal_bests.forEach(function (pb) { byDistance[pb.distance] = pb; });
    if (pbOpenDistance && !(byDistance[pbOpenDistance] && byDistance[pbOpenDistance].records.length)) {
      pbOpenDistance = null;
    }

    var grid = h('div', 'pb-grid');
    ov.personal_bests.forEach(function (pb) {
      grid.appendChild(pbCard(pb));
    });
    node.appendChild(grid);
    if (pbOpenDistance) node.appendChild(pbDetail(byDistance[pbOpenDistance]));
  }

  function pbCard(pb) {
    var count = pb.records.length;
    var expandable = count > 0;
    var card = h(expandable ? 'button' : 'div', 'pb-card');
    if (count === 0) card.className += ' is-empty';
    if (expandable) {
      card.type = 'button';
      card.className += ' is-expandable' + (pbOpenDistance === pb.distance ? ' is-open' : '');
      card.setAttribute('aria-expanded', pbOpenDistance === pb.distance ? 'true' : 'false');
      card.setAttribute('aria-controls', 'pb-detail');
      card.addEventListener('click', function () {
        pbOpenDistance = pbOpenDistance === pb.distance ? null : pb.distance;
        renderOverviewPbs(state.overview);
        var target = pbOpenDistance ? $('pb-detail') : null;
        if (target) target.focus({ preventScroll: true });
      });
    }

    var top = h('div', 'pb-card-head');
    top.appendChild(h('span', 'pb-distance', pb.label));
    if (expandable) top.appendChild(h('span', 'pb-count', count > 1 ? '共 ' + count + ' 場' : ''));
    card.appendChild(top);

    if (count === 0) {
      card.appendChild(h('div', 'pb-empty-text', '尚無紀錄'));
      return card;
    }
    var best = pb.records[0];
    card.appendChild(h('div', 'pb-time', C.formatDuration(best.time_sec)));
    card.appendChild(h('div', 'pb-pace', C.formatPace(best.pace_sec_per_km) + '/km'));
    card.appendChild(h('div', 'pb-date', best.date));
    card.appendChild(h('div', 'pb-vdot', 'VDOT ' + C.formatNumber(best.vdot, 1)));
    var name = h('div', 'pb-name', pbCardName(best));
    name.title = best.name || '';
    card.appendChild(name);
    return card;
  }

  function pbDetail(pb) {
    var panel = h('div', 'pb-detail');
    panel.id = 'pb-detail';
    panel.tabIndex = -1;
    var head = h('div', 'pb-detail-head');
    head.appendChild(h('h3', 'pb-detail-title', pb.label + ' 歷史成績（共 ' + pb.records.length + ' 場'
      + (pb.records.length > 1 ? '，由快到慢）' : '）')));
    var close = h('button', 'pb-detail-close', '✕');
    close.type = 'button';
    close.setAttribute('aria-label', '收合 ' + pb.label + ' 歷史成績');
    close.addEventListener('click', function () {
      pbOpenDistance = null;
      renderOverviewPbs(state.overview);
    });
    head.appendChild(close);
    panel.appendChild(head);

    var list = h('ol', 'pb-list');
    pb.records.forEach(function (r, i) {
      var item = h('li');
      var row = h('button', 'pb-row' + (i === 0 ? ' is-best' : ''));
      row.type = 'button';
      row.setAttribute('aria-label', '第 ' + (i + 1) + ' 名 ' + C.formatDuration(r.time_sec) + '，' + r.date + '，' + (r.name || '') + '，檢視單場分析');
      row.appendChild(h('span', 'pb-row-rank', String(i + 1)));
      var time = h('span', 'pb-row-time', C.formatDuration(r.time_sec));
      if (r.official) {
        var manual = h('span', 'pb-manual', '手動');
        manual.title = '時間為人工填入（大會成績或現場計時），非手錶紀錄';
        time.appendChild(manual);
      }
      row.appendChild(time);
      row.appendChild(h('span', 'pb-row-pace', C.formatPace(r.pace_sec_per_km) + '/km'));
      row.appendChild(h('span', 'pb-row-date', r.date));
      row.appendChild(h('span', 'pb-row-name', r.name || ''));
      row.appendChild(h('span', 'pb-row-vdot', 'VDOT ' + C.formatNumber(r.vdot, 1)));
      row.addEventListener('click', function () { navigate('sessions', r.id); });
      item.appendChild(row);
      list.appendChild(item);
    });
    panel.appendChild(list);
    return panel;
  }

  function renderOverview() {
    var ov = state.overview;
    if (!ov) return; // 還沒回來：載入中訊息由 loadOverview() 放
    // 重繪會換掉所有 ⓘ 按鈕；開著的說明小視窗若不關，會失去對應的按鈕
    closeInfoPopover(false);
    if (!ov.available) {
      OVERVIEW_NODE_IDS.forEach(function (id) { showUnavailable($(id), ov.reason); });
      return;
    }
    renderOverviewStrip(ov);
    renderOverviewKpis(ov);
    renderOverviewVolume(ov);
    renderOverviewZones(ov);
    renderOverviewPlan();
    renderOverviewPbs(ov);
  }

  function loadOverview() {
    OVERVIEW_NODE_IDS.forEach(function (id) { showLoading($(id), '載入總覽'); });
    loadOverviewWeek();
    api('/api/overview?range=' + state.range).then(function (data) {
      state.overview = data;
      renderOrDefer('overview', renderOverview);
    }).catch(function (err) {
      OVERVIEW_NODE_IDS.forEach(function (id) { showError($(id), err.message); });
    });
  }

  // ------------------------------------------------------------ 頁首資訊

  function renderMeta() {
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

  // ------------------------------------------------------------ 載入流程

  /**
   * 載入某個 range 的所有資料，然後重繪。
   * 這是「設定 range → 重抓 → 重繪」的單一路徑，未來加滑動手勢直接呼叫 setRange 即可。
   */
  function loadRangeData() {
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

  function loadSessionDetail(id) {
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

  // ------------------------------------------------------------ 狀態切換入口（供互動與未來手勢共用）

  /**
   * 設定時間區間並重新載入。
   * @param {string} range '7d'|'30d'|'90d'|'1y'|'all'，或自訂區間
   *   'custom:YYYY-MM-DD:YYYY-MM-DD'（見後端 dashboard_queries.CUSTOM_RANGE_PREFIX）
   */
  function setRange(range) {
    if (!isValidRangeKey(range)) return;
    if (state.range === range) return;
    state.range = range;
    saveRangeToSettings(range);
    syncRangeToUrl(range);
    updateRangeButtons();
    loadRangeData();
  }

  function saveRangeToSettings(range) {
    if (window.Theme && window.Theme.saveSettings) window.Theme.saveSettings({ range: range });
  }

  /**
   * 把目前的 range 同步進網址 query（用 replaceState，不新增瀏覽紀錄，
   * 避免每切一次 range 使用者按上一頁就要按很多次）。讓 URL 可以直接
   * 分享／加書籤，開啟時由 restoreInitialRange() 讀回。
   */
  function syncRangeToUrl(range) {
    if (!window.history || !window.history.replaceState) return;
    var url = new URL(window.location.href);
    url.searchParams.set('range', range);
    window.history.replaceState(null, '', url.toString());
  }

  /**
   * 選定場次並重新載入該場詳細資料。
   * @param {number} id
   */
  function selectSession(id) {
    if (id === null || id === undefined || isNaN(id)) return;
    navigate('sessions', id);
  }

  function updateRangeButtons() {
    var buttons = document.querySelectorAll('#range-switch .range-btn');
    var isCustom = state.range.indexOf(CUSTOM_RANGE_PREFIX) === 0;
    Array.prototype.forEach.call(buttons, function (btn) {
      var active = btn.getAttribute('data-range') === state.range;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    var toggle = $('custom-range-toggle');
    if (toggle) toggle.classList.toggle('is-active', isCustom);
  }

  /**
   * 依 /api/meta 回傳的 ranges 動態產生按鈕（後端 RANGE_LABELS 是唯一真實
   * 來源）。找不到 meta.ranges 時退回 FALLBACK_RANGES 的固定標籤，確保
   * meta 請求失敗時使用者仍能操作預設區間。
   */
  function renderRangeButtons() {
    var container = $('range-switch');
    if (!container) return;
    clear(container);
    var ranges = (state.meta && state.meta.ranges) || FALLBACK_RANGES.map(function (key) {
      return { key: key, label: key };
    });
    ranges.forEach(function (r) {
      var btn = h('button', 'range-btn', r.label);
      btn.type = 'button';
      btn.setAttribute('data-range', r.key);
      btn.setAttribute('aria-pressed', r.key === state.range ? 'true' : 'false');
      btn.addEventListener('click', function () { setRange(r.key); });
      container.appendChild(btn);
    });
    updateRangeButtons();
  }

  /**
   * 自訂區間面板：輸入 min/max 依 meta.data_bounds 限制在實際有資料的
   * 範圍內，避免選到必然空白的區間。套用時關閉面板並呼叫既有的 setRange()
   * 單一入口——自訂區間與預設區間共用同一條「設定 range → 重抓 → 重繪」
   * 路徑，不另開一條。
   */
  function bindCustomRangePanel() {
    var toggle = $('custom-range-toggle');
    var panel = $('custom-range-panel');
    var startInput = $('custom-range-start');
    var endInput = $('custom-range-end');
    var applyBtn = $('custom-range-apply');
    if (!toggle || !panel || !startInput || !endInput || !applyBtn) return;

    function closePanel() {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
    }
    function openPanel() {
      var bounds = state.meta && state.meta.data_bounds;
      if (bounds) {
        startInput.min = bounds.earliest_date;
        startInput.max = bounds.latest_date;
        endInput.min = bounds.earliest_date;
        endInput.max = bounds.latest_date;
      }
      panel.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
    }

    toggle.addEventListener('click', function () {
      if (panel.hidden) openPanel(); else closePanel();
    });
    applyBtn.addEventListener('click', function () {
      if (!startInput.value || !endInput.value) return;
      if (startInput.value > endInput.value) return; // 起訖顛倒，交給使用者自行修正，不代為調換
      setRange(CUSTOM_RANGE_PREFIX + startInput.value + ':' + endInput.value);
      closePanel();
    });
    // 點擊面板外部收合（但不吃掉面板內部的點擊，也不影響其他委派事件）
    document.addEventListener('click', function (evt) {
      var container = $('custom-range');
      if (container && !container.contains(evt.target)) closePanel();
    });
  }

  // ------------------------------------------------------------ 路由（App 殼）
  //
  // hash 路由 `#/view[/arg]`：
  //   #/today  #/calendar  #/calendar/2026-09  #/sessions  #/sessions/1083
  //   #/load   #/wellness
  // 五個 view 是 <section class="view" hidden>，切換只動 hidden——不重建 DOM，
  // 圖表與既有容器 id 都原地保留。
  //
  // 為什麼不在切換時無條件重畫：widthOf() 對 display:none 的容器回 0，
  // 隱藏 view 內畫出來的 SVG 寬度會是 0。所以資料載入只渲染「目前」view，
  // 其餘標髒（state.dirty），切過去時才補畫。

  /** 解析 location.hash → {view, arg}。無效或空的 hash 一律回預設 view。 */
  function parseHash() {
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
  function navigate(view, arg) {
    if (!VIEWS[view]) return;
    var hash = '#/' + view + (arg !== null && arg !== undefined && arg !== '' ? '/' + arg : '');
    if (window.location.hash === hash) {
      applyRoute(); // hash 沒變不會觸發 hashchange，直接套用（例如重選同一場）
      return;
    }
    window.location.hash = hash;
  }

  /** 讀目前 hash 並套用到畫面：顯示對應 view、更新導覽高亮、處理參數。 */
  function applyRoute() {
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
  function applySessionRouteArg(arg) {
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
  function renderDirtyView(view) {
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
  function renderOrDefer(key, renderFn) {
    if (state.view === key) renderFn();
    else state.dirty[key] = true;
  }

  /**
   * 手機的訓練紀錄「清單 → 明細」兩段式：有選定場次時顯示明細、否則
   * 顯示清單。桌機兩欄並存，這個 class 在 CSS 的手機斷點才生效。
   */
  function updateSessionsMobileStage() {
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

  // ------------------------------------------------------------ 資料同步
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

  var syncState = { running: false };

  function syncButtons() {
    return document.querySelectorAll('[data-action="sync"]');
  }

  function setSyncBusy(busy, label) {
    syncState.running = busy;
    Array.prototype.forEach.call(syncButtons(), function (btn) {
      btn.disabled = busy;
      btn.classList.toggle('is-syncing', busy);
      // 右上角是純 icon 鈕（無文字節點），只有設定頁那顆要換字
      var textNode = btn.querySelector('.setting-btn-text');
      if (textNode && label) textNode.textContent = label;
    });
  }

  function setSyncMessage(text, kind) {
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
  function describeSyncFailure(result) {
    var reason = result.reason || '同步失敗，原因不明';
    if (result.rate_limited) {
      return '已觸發 Garmin 的請求限制，請稍後再試（不要連續重試）。' + reason;
    }
    return reason;
  }

  function requestSync() {
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
        // 同步會改變資料，今日卡與行事曆必須重讀，否則畫面還停在同步前
        loadToday();
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
  function bindSyncButtons() {
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

  // ------------------------------------------------------------ 系統設定 view
  //
  // N-19 §4。所有設定都存在 theme.js 的 `rc-dashboard-settings` 這一個
  // localStorage key 裡（透過 getSettings/saveSettings），不另開新 key。
  // 主題三選一與「與系統同步」開關由 theme.js 自己綁定與連動，本節只處理
  // AI 教練面板預設與資料同步兩塊。

  function renderSettingsSync() {
    renderSyncFreshness('settings-sync', false);
  }

  /**
   * 「面板預設開啟」開關。與面板目前的開合狀態共用 settings.coachPanel
   * 一個欄位（§4.2 指定），所以兩邊必須雙向連動：
   *   - 從設定頁切換 → 立刻套用到面板，否則開關說「預設開啟」但面板沒反應
   *   - 從面板收合／展開 → 由 setCoachPanel 呼叫 syncCoachSettingToggle 回寫勾選狀態
   * 只用一個欄位而不另開「預設值」欄位，是因為使用者最後一次的開合本來就是
   * 他要的預設；分成兩個欄位反而會出現「設定說開、實際記住關」的矛盾。
   */
  function bindCoachSetting() {
    var toggle = $('setting-coach-open');
    if (!toggle) return;
    syncCoachSettingToggle();
    toggle.addEventListener('change', function () {
      setCoachPanel(toggle.checked, true);
    });
  }

  function syncCoachSettingToggle() {
    var toggle = $('setting-coach-open');
    if (!toggle) return;
    var settings = (window.Theme && window.Theme.getSettings) ? window.Theme.getSettings() : null;
    toggle.checked = !(settings && settings.coachPanel === 'collapsed');
  }

  function bindRouter() {
    window.addEventListener('hashchange', applyRoute);
    var back = $('session-back');
    if (back) back.addEventListener('click', function () { navigate('sessions'); });
  }

  // ------------------------------------------------------------ AI 教練面板（S1 殼）
  //
  // S1 只負責「殼」：三種寬度的形態（桌機常駐欄／平板抽屜／手機全螢幕 sheet）
  // 與收合狀態的存讀。對話、串流、工具 chip 屬 S2／S3 的 coach.js，這裡不碰。
  // 收合偏好沿用既有的 Theme.saveSettings，不另開 localStorage key。

  /**
   * 設定面板展開狀態。
   * @param {boolean} open
   * @param {boolean} [persist] 是否寫入偏好（程式啟動時的還原不需重寫）
   */
  function setCoachPanel(open, persist) {
    document.body.classList.toggle('coach-open', open);
    var panel = $('coach-panel');
    var rail = $('coach-rail');
    var scrim = $('coach-scrim');
    var toggle = $('coach-toggle');
    if (panel) panel.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (rail) rail.hidden = open;
    if (scrim) scrim.hidden = !open;
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (persist && window.Theme && window.Theme.saveSettings) {
      window.Theme.saveSettings({ coachPanel: open ? 'open' : 'collapsed' });
      syncCoachSettingToggle(); // 設定頁的開關與面板共用同一個欄位，不可各記一份
    }
  }

  function bindCoachPanel() {
    var settings = (window.Theme && window.Theme.getSettings) ? window.Theme.getSettings() : null;
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
  var resizeTimer = null;
  function bindResize() {
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

  // ------------------------------------------------------------ 啟動

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
    var settings = window.Theme && window.Theme.getSettings ? window.Theme.getSettings() : null;
    var fromSettings = settings && settings.range;
    if (fromSettings && (FALLBACK_RANGES.indexOf(fromSettings) !== -1 || CUSTOM_RANGE_PATTERN.test(fromSettings))) {
      state.range = fromSettings;
    }
  }

  function init() {
    if (window.Theme) window.Theme.init();
    restoreInitialRange();
    bindResize();
    bindTooltipDelegation();
    bindInfoTipDismiss();
    bindCustomRangePanel();
    bindCoachPanel();
    bindCoachSetting();
    bindSyncButtons();
    bindRouter();
    renderCalendarLegend();

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

    loadToday();
    loadWeekStrip();
    loadTodayKpi();
    loadRecentSessions();
    loadRangeData();
  }

  // 對外只暴露狀態切換入口，方便日後接手勢／鍵盤等互動
  window.DashboardApp = {
    setRange: setRange,
    selectSession: selectSession,
    navigate: navigate,
    getState: function () { return state; },
    // 供 coach.js 的 CoachPanel.runAnalysis()／showAnalyses() 打開面板（不寫入偏好：這是一次性動作）
    openCoachPanel: function () { setCoachPanel(true, false); },

    /**
     * 目前的檢視脈絡，供 AI 教練面板（S3 的 coach.js）產生「問這一頁」
     * 的建議問題、並在送出時附上〔檢視脈絡〕行。
     *
     * 刻意只回「使用者在看什麼」，不回任何數據——面板不在前端拼數據餵
     * 模型，模型自己呼叫 MCP 工具拿含信賴度的事實（D.1 §7.3 三欄位契約）。
     * @returns {{view:string, range:string, sessionId:?number, month:?string, date:?string}}
     */
    getViewContext: function () {
      return {
        view: state.view,
        range: state.range,
        sessionId: state.view === 'sessions' ? state.selectedSessionId : null,
        month: state.view === 'calendar' ? state.calendarMonth : null,
        date: state.today ? state.today.date : null
      };
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
