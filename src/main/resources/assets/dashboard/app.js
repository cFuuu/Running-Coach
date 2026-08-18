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
 *   若 window.MOCK_API 存在且未被 ?mock=0 停用，所有請求改走假資料（見 mock-data.js）。
 *   整合真實後端時，刪掉 index.html 內 mock-data.js 的 <script> 即可。
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
    meta: null
  };

  var VALID_RANGES = ['7d', '30d', '90d', '1y', 'all'];

  // 八個身體狀況指標。契約規定不得呈現的那組電池指標（實測 100% NULL）已刻意排除。
  var WELLNESS_METRICS = [
    { key: 'hrv_ms', label: 'HRV', unit: 'ms', decimals: 1 },
    { key: 'resting_hr_bpm', label: '靜止心率', unit: 'bpm', decimals: 0 },
    { key: 'spo2_pct', label: '血氧', unit: '%', decimals: 0 },
    { key: 'sleep_score', label: '睡眠分數', unit: '', decimals: 0 },
    { key: 'training_readiness_score', label: '訓練準備度', unit: '', decimals: 0 },
    { key: 'stress_avg', label: '壓力（睡眠期間）', unit: '', decimals: 0 },
    { key: 'all_day_stress_avg', label: '壓力（全天）', unit: '', decimals: 0 },
    { key: 'steps', label: '步數', unit: '步', decimals: 0 }
  ];

  var WORKOUT_TYPE_LABEL = {
    easy: '輕鬆跑',
    tempo: '節奏跑',
    interval: '間歇',
    lsd: '長距離慢跑',
    race: '比賽',
    recovery: '恢復跑',
    unknown: '未分類'
  };

  var SOURCE_LABEL = { auto: '自動判定', manual: '手動設定' };

  // ------------------------------------------------------------ 資料存取層

  var useMock = (function () {
    var params = new URLSearchParams(window.location.search);
    var flag = params.get('mock');
    if (flag === '0') return false;              // 強制走真實 API
    if (flag === '1') return !!window.MOCK_API;  // 強制走假資料
    return !!window.MOCK_API;                    // 預設：有假資料就用假資料
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
  // 三組互不同步，因為 X 軸基準不同（單場分析＝經過秒數；跨場趨勢／
  // 身體狀況＝日期字串，兩者字典序恰好與時間序一致，可直接比較）。
  var SYNC_GROUPS = ['session-body', 'trend-charts', 'wellness-charts'];

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

  // 數值標籤的粗估字元寬度／高度（viewBox 單位，對應 charts.js 的 11px tick-label
  // 字級換算）。SVG 文字無法在畫之前用 DOM API 量實際寬度，用等寬粗估即可，
  // 標籤本身不要求像素級精準，只要不明顯溢出。
  var SYNC_LABEL_CHAR_W = 6.4;
  var SYNC_LABEL_PAD_X = 5;
  var SYNC_LABEL_H = 16;

  /**
   * 在單張圖的 SVG 上畫出同步游標：跨越整個繪圖高度的垂直線、（若該圖在
   * 這個邏輯 X 有值）一個圓點、以及貼在圓點旁的數值標籤（背景框＋文字，
   * 仿 Garmin 風格——每張圖各自顯示自己的數值，不集中成單一 tooltip）。
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
      var labelY = y !== null && y !== undefined ? y : box.top + 12;
      var labelW = tip.length * SYNC_LABEL_CHAR_W + SYNC_LABEL_PAD_X * 2;
      // 貼在垂直線右側；靠近圖表右緣時翻到左側，避免超出 viewBox
      var atRightEdge = x + 6 + labelW > box.left + box.width;
      var labelX = atRightEdge ? x - 6 - labelW : x + 6;
      var labelTop = Math.min(Math.max(labelY - SYNC_LABEL_H / 2, box.top), box.top + box.height - SYNC_LABEL_H);

      var group = svgEl('g', { class: 'sync-cursor-label' });
      group.appendChild(svgEl('rect', {
        x: labelX, y: labelTop, width: labelW, height: SYNC_LABEL_H, rx: 3
      }));
      var textNode = svgEl('text', {
        x: labelX + labelW / 2, y: labelTop + SYNC_LABEL_H / 2 + 4, 'text-anchor': 'middle'
      });
      textNode.textContent = tip;
      group.appendChild(textNode);
      layer.appendChild(group);
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
          showTooltipText(meta.tips[idx], evt.clientX, evt.clientY);
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

  // ------------------------------------------------------------ 區塊一：單場分析

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

  // ------------------------------------------------------------ 區塊二：跨場趨勢

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

  function renderWellness() {
    var node = $('wellness-charts');
    clear(node);
    if (!state.wellness) return;

    var metrics = state.wellness.metrics || {};
    var startDate = state.wellness.start_date;
    var endDate = state.wellness.end_date;

    WELLNESS_METRICS.forEach(function (def) {
      var m = metrics[def.key];
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
        formatter: function (v) { return C.formatNumber(v, def.decimals); },
        lineClass: 'line-metric-' + def.key,
        dotClass: 'dot-metric',
        containerWidth: widthOf(node)
      })));

      var latest = points[points.length - 1];
      card.appendChild(h('div', 'chart-note',
        '最新（' + latest.date + '）：' + C.formatNumber(latest.value, def.decimals)
        + (def.unit ? ' ' + def.unit : '') + '　・　共 ' + points.length + ' 天有資料'));
      node.appendChild(card);
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
   * @param {string} range '7d'|'30d'|'90d'|'1y'|'all'
   */
  function setRange(range) {
    if (VALID_RANGES.indexOf(range) === -1) return;
    if (state.range === range) return;
    state.range = range;
    updateRangeButtons();
    loadRangeData();
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
    var buttons = document.querySelectorAll('.range-btn');
    Array.prototype.forEach.call(buttons, function (btn) {
      var active = btn.getAttribute('data-range') === state.range;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function bindRangeButtons() {
    var buttons = document.querySelectorAll('.range-btn');
    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener('click', function () {
        setRange(btn.getAttribute('data-range'));
      });
    });
    updateRangeButtons();
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
      }, 200);
    });
  }

  // ------------------------------------------------------------ 啟動

  function init() {
    if (window.Theme) window.Theme.init();
    bindRangeButtons();
    bindResize();
    bindTooltipDelegation();

    api('/api/meta').then(function (meta) {
      state.meta = meta;
      if (meta && meta.athlete && meta.athlete.id !== undefined && meta.athlete.id !== null) {
        state.athleteId = meta.athlete.id;
      }
      renderMeta();
    }).catch(function (err) {
      var notice = $('notice-bar');
      clear(notice);
      notice.appendChild(h('span', 'state-error-inline', '無法取得基本資訊：' + err.message));
    });

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
