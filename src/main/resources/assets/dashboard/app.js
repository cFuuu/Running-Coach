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
    calendarExpanded: false,
    calendarMonth: null,   // 'YYYY-MM'，展開月曆時才需要，預設當月
    calendarData: null,    // /api/calendar 回傳的月曆資料（展開後才載入）
    weekDays: null         // /api/calendar 的本週資料（週條用）
  };

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
    { key: 'hrv_ms', label: 'HRV', unit: 'ms', decimals: 1, format: 'number', default_hidden: false },
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
    race: '比賽',
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
    var level = (fact.confidence && fact.confidence.level) || 'high';
    var badge = h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level);
    var biasDetails = (fact.confidence && fact.confidence.biases || [])
      .map(function (b) { return b.detail; }).join('；');
    badge.title = (fact.source || '') + (biasDetails ? '　' + biasDetails : '');
    wrap.appendChild(badge);
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
        var hrvRow = h('div', 'today-fact-row');
        hrvRow.appendChild(h('span', 'today-fact-label', 'HRV'));
        hrvRow.appendChild(factValueNode(data.hrv.today_ms, function (v) { return C.formatNumber(v, 1); }, ' ms'));
        loadBlock.appendChild(hrvRow);
        if (data.hrv.delta_pct) {
          var deltaRow = h('div', 'today-fact-row');
          deltaRow.appendChild(h('span', 'today-fact-label', '較 7 日均值'));
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
      var btn = h('button', 'session-card', null);
      btn.type = 'button';
      var text = la.date + '　' + (ACTIVITY_TYPE_LABEL[la.activity_type] || la.activity_type);
      if (la.distance_km) text += '　' + C.formatNumber(la.distance_km, 1) + ' km';
      if (la.avg_pace_sec_per_km) text += '　' + C.formatPace(la.avg_pace_sec_per_km) + '/km';
      btn.textContent = text;
      if (la.activity_type && RUNNING_ACTIVITY_TYPES_CLIENT.indexOf(la.activity_type) !== -1) {
        btn.addEventListener('click', function () { selectSession(la.id); scrollToSection('panel-session'); });
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

  function scrollToSection(id) {
    var el = $(id);
    if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function loadToday() {
    showLoading($('today-body'), '載入今日狀態');
    api('/api/today').then(function (data) {
      state.today = data;
      renderToday();
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
    var hrvCard = statCard('HRV 變化',
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

  /** 取「今天」所在週的週一（UTC，與既有 weeklyVolume() 週一為週首慣例一致）。 */
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
          selectSession(runnable.id);
          scrollToSection('panel-session');
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
    var monday = mondayOf(todayISO());
    var sunday = addDaysISO(monday.toISOString().slice(0, 10), 6);
    var start = monday.toISOString().slice(0, 10);
    api('/api/calendar?start=' + start + '&end=' + sunday).then(function (data) {
      state.weekDays = data.days || [];
      renderWeekStrip();
    }).catch(function (err) {
      showError($('week-strip'), err.message);
    });
  }

  /** 行事曆展開時用的月份邊界（YYYY-MM 的第一天所在週一 ~ 最後一天所在週日）。 */
  function monthGridRange(yearMonth) {
    var firstOfMonth = yearMonth + '-01';
    var d = new Date(firstOfMonth + 'T00:00:00Z');
    var nextMonth = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
    var lastOfMonth = new Date(nextMonth.getTime() - 86400000).toISOString().slice(0, 10);
    return {
      start: mondayOf(firstOfMonth).toISOString().slice(0, 10),
      end: (function () {
        var sundayBase = mondayOf(lastOfMonth);
        sundayBase.setUTCDate(sundayBase.getUTCDate() + 6);
        return sundayBase.toISOString().slice(0, 10);
      })(),
      monthStart: firstOfMonth,
      monthEnd: lastOfMonth
    };
  }

  function renderCalendarBody() {
    var node = $('calendar-body');
    clear(node);
    if (!state.calendarExpanded) return;
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
        var runnable = firstRunningActivity(day.activities);
        if (runnable) {
          cell.classList.add('is-clickable');
          cell.addEventListener('click', function (id) {
            return function () { selectSession(id); scrollToSection('panel-session'); };
          }(runnable.id));
        }
        if (day.activities.length > 0) cell.appendChild(activityDots(day.activities));
        cell.title = cursor + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
      }
      grid.appendChild(cell);
      cursor = addDaysISO(cursor, 1);
    }
    node.appendChild(grid);
  }

  function loadCalendarBody() {
    var range = monthGridRange(state.calendarMonth);
    api('/api/calendar?start=' + range.start + '&end=' + range.end).then(function (data) {
      state.calendarData = data;
      renderCalendarBody();
    }).catch(function (err) {
      showError($('calendar-body'), err.message);
    });
  }

  function shiftCalendarMonth(delta) {
    var d = new Date(state.calendarMonth + '-01T00:00:00Z');
    d.setUTCMonth(d.getUTCMonth() + delta);
    state.calendarMonth = d.toISOString().slice(0, 7);
    loadCalendarBody();
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

  function bindCalendarToggle() {
    var toggle = $('calendar-toggle');
    if (!toggle) return;
    toggle.addEventListener('click', function () {
      state.calendarExpanded = !state.calendarExpanded;
      toggle.textContent = state.calendarExpanded ? '收合月曆 ▴' : '展開月曆 ▾';
      toggle.setAttribute('aria-expanded', state.calendarExpanded ? 'true' : 'false');
      var body = $('calendar-body');
      body.hidden = !state.calendarExpanded;
      if (state.calendarExpanded && !state.calendarMonth) {
        state.calendarMonth = todayISO().slice(0, 7);
      }
      if (state.calendarExpanded && !state.calendarData) {
        loadCalendarBody();
      } else {
        renderCalendarBody();
      }
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
      renderLoad();
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

    renderSessionList();
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
      renderTrends();

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
      renderWellness();
    }).catch(function (err) {
      showError(wellnessNode, err.message);
    });

    // 訓練負荷（獨立於場次/身體狀況，走同一個 range）
    loadLoadSeries();
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
      renderSessionDetail();
      renderSessionList(); // 更新選取樣式
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
    state.selectedSessionId = id;
    renderSessionPicker();
    renderSessionList();
    renderRecoveryImpact();
    loadSessionDetail(id);
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

  /**
   * 導覽列高亮：用 IntersectionObserver 追蹤哪個區塊目前在可視範圍內
   * 最靠近頁首，對應的導覽連結加上 is-active。純視覺輔助，不影響任何
   * 資料載入或狀態——找不到 IntersectionObserver（極舊瀏覽器）時直接
   * 跳過，導覽列仍可正常點擊跳轉，只是不會高亮目前區塊。
   */
  function bindSectionNav() {
    var nav = $('section-nav');
    if (!nav) return;
    var links = nav.querySelectorAll('.section-nav-link');

    Array.prototype.forEach.call(links, function (link) {
      link.addEventListener('click', function (evt) {
        var targetId = link.getAttribute('data-nav-target');
        var target = $(targetId);
        if (target) {
          evt.preventDefault();
          scrollToSection(targetId);
        }
      });
    });

    if (typeof IntersectionObserver === 'undefined') return;

    function setActive(id) {
      Array.prototype.forEach.call(links, function (link) {
        link.classList.toggle('is-active', link.getAttribute('data-nav-target') === id);
      });
    }

    var navHeight = nav.offsetHeight || 44;
    var observer = new IntersectionObserver(function (entries) {
      var visible = entries.filter(function (e) { return e.isIntersecting; });
      if (visible.length === 0) return;
      // 取最靠近頁首（top 最小）的可見區塊
      visible.sort(function (a, b) { return a.boundingClientRect.top - b.boundingClientRect.top; });
      setActive(visible[0].target.id);
    }, { rootMargin: '-' + (navHeight + 4) + 'px 0px -70% 0px' });

    ['panel-today', 'panel-calendar', 'panel-session', 'panel-load', 'panel-wellness'].forEach(function (id) {
      var el = $(id);
      if (el) observer.observe(el);
    });
  }

  // 視窗尺寸改變時重繪（X 軸刻度數量依容器寬度決定，需重算）
  var resizeTimer = null;
  function bindResize() {
    window.addEventListener('resize', function () {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (state.sessionDetail) renderSessionDetail();
        if (state.sessions.length > 0) renderTrends();
        if (state.wellness) renderWellness();
        if (state.loadSeries) renderLoad();
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
    bindCustomRangePanel();
    bindCalendarToggle();
    bindSectionNav();
    renderCalendarLegend();

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
    loadRangeData();
  }

  // 對外只暴露狀態切換入口，方便日後接手勢／鍵盤等互動
  window.DashboardApp = {
    setRange: setRange,
    selectSession: selectSession,
    getState: function () { return state; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
