/**
 * charts.js — 純畫圖函式（手繪 SVG，無任何第三方圖表庫、無 CDN）
 *
 * 【架構契約】
 *   本檔只放「吃資料 → 回傳 SVG 元素」的純函式：
 *     - 不做 fetch
 *     - 不讀寫全域狀態
 *     - 不直接操作 document.getElementById（由 app.js 決定掛到哪）
 *   將來要加滑動手勢等互動，只需在 app.js 改「設定 range → 重抓 → 重繪」的流程，
 *   本檔完全不用動。
 *
 * 【呈現原則】
 *   圖表只呈現數據與趨勢線，**不做任何文字解讀或好壞判斷**。
 *
 * 【RWD】
 *   所有 SVG 一律用 viewBox + preserveAspectRatio，不寫死像素寬；
 *   X 軸刻度依「實際容器寬度」動態決定顯示幾個，避免手機上標籤重疊。
 */
window.Charts = (function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  // 內部繪圖座標系（viewBox 單位），實際顯示尺寸由 CSS 決定
  var VB_WIDTH = 720;

  // ---------------------------------------------------------------- 基礎工具

  function el(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (attrs[k] === null || attrs[k] === undefined) return;
        node.setAttribute(k, String(attrs[k]));
      });
    }
    return node;
  }

  function text(content, attrs) {
    var node = el('text', attrs);
    node.textContent = content;
    return node;
  }

  /**
   * 建立一個具備 viewBox 的 SVG 外框。
   * @param {number} height viewBox 高度（單位非像素，等比縮放）
   * @param {string} label 無障礙用途的圖表名稱
   */
  function createSvg(height, label) {
    var svg = el('svg', {
      viewBox: '0 0 ' + VB_WIDTH + ' ' + height,
      preserveAspectRatio: 'xMidYMid meet',
      role: 'img',
      'aria-label': label,
      class: 'chart-svg'
    });
    return svg;
  }

  /**
   * 依容器實際像素寬度，決定 X 軸最多顯示幾個刻度標籤。
   * 30 天標籤在 390px 手機上會擠成一團，所以必須動態抽稀。
   * @param {number} containerPx 容器寬度（px）；取不到時給保守值
   * @param {number} perLabelPx 每個標籤預估需要的像素寬
   */
  function maxTickCount(containerPx, perLabelPx) {
    var w = containerPx && containerPx > 0 ? containerPx : 320;
    var per = perLabelPx || 56;
    return Math.max(2, Math.floor(w / per));
  }

  /**
   * 從一個序列中平均挑出至多 maxCount 個索引（一定包含頭尾）。
   */
  function pickTickIndexes(length, maxCount) {
    if (length <= 0) return [];
    if (length <= maxCount) {
      var all = [];
      for (var i = 0; i < length; i += 1) all.push(i);
      return all;
    }
    var step = (length - 1) / (maxCount - 1);
    var out = [];
    for (var k = 0; k < maxCount; k += 1) {
      var idx = Math.round(k * step);
      if (out.indexOf(idx) === -1) out.push(idx);
    }
    return out;
  }

  function niceExtent(values, padRatio) {
    var nums = values.filter(function (v) { return typeof v === 'number' && isFinite(v); });
    if (nums.length === 0) return null;
    var min = Math.min.apply(null, nums);
    var max = Math.max.apply(null, nums);
    if (min === max) {
      var delta = Math.abs(min) * 0.1 || 1;
      return { min: min - delta, max: max + delta };
    }
    var pad = (max - min) * (padRatio === undefined ? 0.12 : padRatio);
    return { min: min - pad, max: max + pad };
  }

  // ---------------------------------------------------------------- 格式化

  /** 秒 → m:ss（配速用） */
  function formatPace(sec) {
    if (sec === null || sec === undefined || !isFinite(sec)) return '—';
    var m = Math.floor(sec / 60);
    var s = Math.round(sec % 60);
    if (s === 60) { m += 1; s = 0; }
    return m + ':' + String(s).padStart(2, '0');
  }

  /** 秒 → h:mm:ss 或 mm:ss（時長用） */
  function formatDuration(sec) {
    if (sec === null || sec === undefined || !isFinite(sec)) return '—';
    var total = Math.round(sec);
    var h = Math.floor(total / 3600);
    var m = Math.floor((total % 3600) / 60);
    var s = total % 60;
    if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    return m + ':' + String(s).padStart(2, '0');
  }

  /** 'YYYY-MM-DD' → 'M/D'（X 軸刻度用，空間有限故不補零） */
  function shortDate(iso) {
    if (!iso) return '';
    var parts = String(iso).split('-');
    if (parts.length < 3) return iso;
    return parseInt(parts[1], 10) + '/' + parseInt(parts[2], 10);
  }

  /** 'YYYY-MM-DD' → 'MM/DD'（同步游標標籤用，補零對齊較好讀） */
  function shortDatePadded(iso) {
    if (!iso) return '';
    var parts = String(iso).split('-');
    if (parts.length < 3) return iso;
    return parts[1] + '/' + parts[2];
  }

  function formatNumber(v, decimals) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return Number(v).toFixed(decimals === undefined ? 0 : decimals);
  }

  // ---------------------------------------------------------------- 共用繪製

  function drawAxes(svg, box) {
    svg.appendChild(el('line', {
      x1: box.left, y1: box.top + box.height, x2: box.left + box.width, y2: box.top + box.height,
      class: 'axis-line'
    }));
    svg.appendChild(el('line', {
      x1: box.left, y1: box.top, x2: box.left, y2: box.top + box.height,
      class: 'axis-line'
    }));
  }

  /** 畫 Y 軸格線與刻度標籤 */
  function drawYTicks(svg, box, extent, formatter, tickCount) {
    var count = tickCount || 4;
    for (var i = 0; i <= count; i += 1) {
      var ratio = i / count;
      var y = box.top + box.height - ratio * box.height;
      var value = extent.min + ratio * (extent.max - extent.min);
      svg.appendChild(el('line', {
        x1: box.left, y1: y, x2: box.left + box.width, y2: y, class: 'grid-line'
      }));
      svg.appendChild(text(formatter(value), {
        x: box.left - 8, y: y + 4, class: 'tick-label', 'text-anchor': 'end'
      }));
    }
  }

  /** 空狀態／不可用狀態的替代畫面（不畫空圖） */
  function emptyBlock(message) {
    var div = document.createElement('div');
    div.className = 'chart-empty';
    div.textContent = message || '無資料';
    return div;
  }

  // ---------------------------------------------------------------- Tooltip／同步游標命中層
  //
  // 本檔仍是純函式：這裡只負責「附加命中資料」，不 addEventListener、不碰
  // document——實際綁事件、畫游標、顯示數值由 app.js 統一處理（見該檔的
  // hover 委派邏輯）。命中資料以兩種形狀提供：
  //
  //   離散模式（attachDiscreteHitLayer）：資料項本來就少（分圈、週、5 個
  //   zone），逐項各畫一個透明矩形，直接帶 tip 文字，app.js 只需命中測試、
  //   用單圖浮動 tooltip 顯示。心率區間圖是離散模式且沒有連續時間軸，維持
  //   單圖 tooltip，不參與下面的跨圖同步游標。
  //
  //   連續模式（buildContinuousHitMeta）：逐秒／長區間的資料點可能有數百到
  //   上千筆，不逐點產生 DOM 元素。回傳一份 {box, xs, logicalXs, yOf, tips}：
  //     - xs         該圖上每個資料點的螢幕 x 座標（viewBox 單位）
  //     - logicalXs  對應的「邏輯 X 值」（單場分析是 elapsed_sec 數字；跨場
  //                  趨勢/身體狀況是 date 字串），是跨圖同步游標的比對基準
  //     - yOf(index) 依索引回傳該點在此圖的 y 座標（畫游標與圓點要用）
  //     - tips       與 xs 一一對應的顯示文字
  //   app.js 在群組內任一圖 hover 時，先用該圖的 xs 反查出 logicalXs，
  //   再用這個 logicalXs 去同群組「每一張圖」各自的 logicalXs 陣列二分搜尋，
  //   找出各圖對應點的 x/y，畫出同一條垂直線與各自的數值——這就是 Garmin
  //   風格「跨圖同步參考線」的資料基礎。單圖內的二分搜尋與群組同步共用同一份
  //   meta，不必為兩種互動各存一份資料。

  /**
   * 離散模式：每個資料項一個透明命中矩形，掛在 svg 最後方。
   * @param {SVGElement} svg
   * @param {Array} items [{x, y, width, height, tip}]（tip 為要顯示的文字）
   */
  function attachDiscreteHitLayer(svg, items) {
    items.forEach(function (item) {
      svg.appendChild(el('rect', {
        x: item.x, y: item.y, width: Math.max(item.width, 0), height: Math.max(item.height, 0),
        class: 'hit', fill: 'transparent', 'data-tip': item.tip
      }));
    });
  }

  /**
   * 連續模式：組出供 app.js 二分搜尋與跨圖同步游標使用的 meta。
   * @param {Object} box {left, top, width, height}
   * @param {Array} xs 遞增排序的螢幕 x 座標（viewBox 單位）
   * @param {Array} logicalXs 與 xs 一一對應的邏輯 X 值（數字或可比較字串）
   * @param {Function} yOf (index) => y 座標；缺值回傳 null（不畫該點）
   * @param {Array} tips 與 xs 一一對應的顯示文字
   */
  function buildContinuousHitMeta(box, xs, logicalXs, yOf, tips) {
    return { box: box, xs: xs, logicalXs: logicalXs, yOf: yOf, tips: tips };
  }

  /**
   * 在 box 範圍內疊一個透明命中矩形並掛上 meta（供 app.js 用
   * event.target.__chartHitMeta 讀取單圖二分搜尋），連續模式專用。
   * 同時把 meta 掛在 svg.__syncMeta——這是群組同步游標的讀取點，與
   * event.target.__chartHitMeta 分開存放：前者「該圖是否參與同步」不該
   * 綁死在某個特定 DOM 元素上（離散模式圖表如分圈圖，主動觸發同步用不到
   * 覆蓋矩形，但仍需要被其他圖的同步游標找到），見下方 attachSyncMeta。
   */
  function attachContinuousHitLayer(svg, box, meta) {
    var rect = el('rect', {
      x: box.left, y: box.top, width: box.width, height: box.height,
      class: 'hit', fill: 'transparent'
    });
    rect.__chartHitMeta = meta;
    svg.appendChild(rect);
    svg.__syncMeta = meta;
  }

  /**
   * 讓一張「離散模式」圖表（沒有主動觸發同步用的覆蓋矩形，例如分圈圖、
   * 週跑量長條圖）也能被同群組其他圖的同步游標找到並顯示對應數值。
   * 與 attachContinuousHitLayer 的差異：這裡不畫覆蓋矩形（保留原本逐項
   * 命中的互動），只掛 meta 供「被動接收」用。
   */
  function attachSyncMeta(svg, meta) {
    svg.__syncMeta = meta;
  }

  // ---------------------------------------------------------------- 1. 分圈配速長條圖

  /**
   * 每公里分圈配速長條圖（配速越快 = 秒數越小，長條越長，故用反向比例呈現）。
   * @param {Array} laps [{lap, distance_km, duration_sec, pace_sec_per_km, avg_hr_bpm}]
   * @param {Object} opts { containerWidth }
   * @returns {SVGElement}
   */
  function lapPaceChart(laps, opts) {
    var options = opts || {};
    var valid = laps.filter(function (l) {
      return typeof l.pace_sec_per_km === 'number' && isFinite(l.pace_sec_per_km);
    });
    var height = 240;
    var svg = createSvg(height, '每公里分圈配速長條圖');
    var box = { left: 52, top: 16, width: VB_WIDTH - 52 - 16, height: height - 16 - 40 };

    if (valid.length === 0) return svg;

    var paces = valid.map(function (l) { return l.pace_sec_per_km; });
    var maxPace = Math.max.apply(null, paces);
    var minPace = Math.min.apply(null, paces);
    // 基準線比最慢圈再慢一點，讓最慢的圈仍看得到長條
    var baseline = maxPace + Math.max((maxPace - minPace) * 0.25, 10);
    var top = minPace - Math.max((maxPace - minPace) * 0.1, 5);

    drawAxes(svg, box);
    // Y 軸：配速（上快下慢）
    drawYTicks(svg, box, { min: baseline, max: top }, function (v) {
      return formatPace(v);
    }, 4);

    var slot = box.width / valid.length;
    var barWidth = Math.max(Math.min(slot * 0.68, 42), 3);
    var tickIdx = pickTickIndexes(valid.length, maxTickCount(options.containerWidth, 40));
    var hitItems = [];
    // 分圈本身沒有 elapsed_sec 欄位，只有各自的 duration_sec；累加算出每圈
    // 「中點」的經過時間，作為與 records-chart（逐秒圖）同步游標比對的
    // 邏輯 X——兩圖同屬單場分析同步群組，見 app.js 的 SYNC_GROUPS。
    var cumulativeSec = 0;
    var lapLogicalXs = [];
    var lapYs = [];

    valid.forEach(function (lap, i) {
      var cx = box.left + slot * (i + 0.5);
      var ratio = (baseline - lap.pace_sec_per_km) / (baseline - top);
      var h = Math.max(ratio * box.height, 1);
      var barY = box.top + box.height - h;
      var bar = el('rect', {
        x: cx - barWidth / 2,
        y: barY,
        width: barWidth,
        height: h,
        rx: 2,
        class: 'bar bar-pace'
      });
      svg.appendChild(bar);
      // 每圈的距離不足 1km 時（最後一圈）用不同樣式標示
      if (typeof lap.distance_km === 'number' && lap.distance_km < 0.95) {
        svg.appendChild(el('rect', {
          x: cx - barWidth / 2,
          y: barY,
          width: barWidth,
          height: h,
          rx: 2,
          class: 'bar bar-partial'
        }));
      }
      if (tickIdx.indexOf(i) !== -1) {
        svg.appendChild(text(String(lap.lap), {
          x: cx, y: box.top + box.height + 18, class: 'tick-label', 'text-anchor': 'middle'
        }));
      }
      var tip = '第 ' + lap.lap + ' 圈 ・ ' + formatNumber(lap.distance_km, 2) + ' km ・ '
        + formatPace(lap.pace_sec_per_km) + '/km'
        + (lap.avg_hr_bpm ? ' ・ ' + lap.avg_hr_bpm + ' bpm' : '');
      var title = el('title');
      title.textContent = tip;
      bar.appendChild(title); // 掛在 bar 本身（而非 svg.lastChild），避免被 bar-partial／tick label 覆蓋掉
      hitItems.push({ x: cx - slot / 2, y: box.top, width: slot, height: box.height, tip: tip });

      var dur = typeof lap.duration_sec === 'number' && isFinite(lap.duration_sec) ? lap.duration_sec : 0;
      lapLogicalXs.push(cumulativeSec + dur / 2);
      lapYs.push(barY);
      cumulativeSec += dur;
    });

    svg.appendChild(text('圈數（km）', {
      x: box.left + box.width / 2, y: height - 6, class: 'axis-title', 'text-anchor': 'middle'
    }));
    attachDiscreteHitLayer(svg, hitItems); // 必須最後 append，見上方架構註解

    // 被動同步 meta：不畫額外覆蓋矩形（保留上面逐圈 hover 的互動），
    // 只供 records-chart 觸發同步時，這張圖能反查對應圈並畫游標。
    var lapTips = valid.map(function (lap) {
      return '第 ' + lap.lap + ' 圈 ' + formatPace(lap.pace_sec_per_km) + '/km';
    });
    attachSyncMeta(svg, buildContinuousHitMeta(
      box,
      valid.map(function (_, i) { return box.left + slot * (i + 0.5); }),
      lapLogicalXs,
      function (i) { return lapYs[i]; },
      lapTips
    ));
    return svg;
  }

  // ---------------------------------------------------------------- 2. 逐秒配速／心率曲線

  /**
   * 逐秒（降頻後）配速與心率雙軸曲線圖。
   * null 值 → 該點跳過並斷線（不補 0）。
   * @param {Array} points [{elapsed_sec, distance_km, hr_bpm, pace_sec_per_km, cadence_spm}]
   */
  function recordsChart(points, opts) {
    var options = opts || {};
    var height = 260;
    var svg = createSvg(height, '配速與心率逐秒曲線圖');
    var box = { left: 52, top: 16, width: VB_WIDTH - 52 - 52, height: height - 16 - 44 };
    if (!points || points.length === 0) return svg;

    var maxElapsed = points[points.length - 1].elapsed_sec || 1;

    // 配速：過濾極端值（起步瞬間可能異常慢），用分位數限制範圍
    var paceVals = points.map(function (p) { return p.pace_sec_per_km; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v) && v > 0; });
    var hrVals = points.map(function (p) { return p.hr_bpm; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });

    var paceExtent = null;
    if (paceVals.length > 0) {
      var sorted = paceVals.slice().sort(function (a, b) { return a - b; });
      var lo = sorted[Math.floor(sorted.length * 0.02)];
      var hi = sorted[Math.floor(sorted.length * 0.98)];
      paceExtent = niceExtent([lo, hi], 0.1);
    }
    var hrExtent = hrVals.length > 0 ? niceExtent(hrVals, 0.12) : null;

    drawAxes(svg, box);

    function xOf(p) {
      return box.left + (p.elapsed_sec / maxElapsed) * box.width;
    }

    // 左軸：配速（上快下慢 → 反轉）
    if (paceExtent) {
      drawYTicks(svg, box, { min: paceExtent.max, max: paceExtent.min }, formatPace, 4);
      svg.appendChild(buildBrokenPath(points, xOf, function (p) {
        if (typeof p.pace_sec_per_km !== 'number' || !isFinite(p.pace_sec_per_km)) return null;
        var clamped = Math.min(Math.max(p.pace_sec_per_km, paceExtent.min), paceExtent.max);
        var ratio = (paceExtent.max - clamped) / (paceExtent.max - paceExtent.min);
        return box.top + box.height - ratio * box.height;
      }, 'line line-pace'));
    }

    // 右軸：心率
    if (hrExtent) {
      for (var i = 0; i <= 4; i += 1) {
        var ratio = i / 4;
        var y = box.top + box.height - ratio * box.height;
        var v = hrExtent.min + ratio * (hrExtent.max - hrExtent.min);
        svg.appendChild(text(formatNumber(v, 0), {
          x: box.left + box.width + 8, y: y + 4, class: 'tick-label tick-label-hr', 'text-anchor': 'start'
        }));
      }
      svg.appendChild(buildBrokenPath(points, xOf, function (p) {
        if (typeof p.hr_bpm !== 'number' || !isFinite(p.hr_bpm)) return null; // null → 斷線
        var r = (p.hr_bpm - hrExtent.min) / (hrExtent.max - hrExtent.min);
        return box.top + box.height - r * box.height;
      }, 'line line-hr'));
    }

    // X 軸：經過時間
    var tickIdx = pickTickIndexes(points.length, maxTickCount(options.containerWidth, 70));
    tickIdx.forEach(function (idx) {
      var p = points[idx];
      svg.appendChild(text(formatDuration(p.elapsed_sec), {
        x: xOf(p), y: box.top + box.height + 18, class: 'tick-label', 'text-anchor': 'middle'
      }));
    });
    svg.appendChild(text('經過時間', {
      x: box.left + box.width / 2, y: height - 6, class: 'axis-title', 'text-anchor': 'middle'
    }));

    // 逐秒資料點多達數百筆，不逐點畫 DOM（見檔頭 Tooltip 命中層說明），
    // 改用連續模式：app.js 依 pointer 的 x 座標二分搜尋 xs 陣列找最近點，
    // logicalXs 用 elapsed_sec（單場分析群組的同步基準，見 index.html
    // #session-body 內各圖共用「經過時間」這條軸）。
    var xs = points.map(xOf);
    var logicalXs = points.map(function (p) { return p.elapsed_sec; });
    var tips = points.map(function (p) {
      var parts = [formatDuration(p.elapsed_sec)];
      if (typeof p.pace_sec_per_km === 'number' && isFinite(p.pace_sec_per_km)) {
        parts.push(formatPace(p.pace_sec_per_km) + '/km');
      }
      if (typeof p.hr_bpm === 'number' && isFinite(p.hr_bpm)) {
        parts.push(p.hr_bpm + ' bpm');
      }
      return parts.join(' ・ ');
    });
    // yOf 優先回傳配速軸的 y（同步游標的圓點畫在配速線上），無配速資料時
    // 退回心率軸，兩者皆無則回傳 null（該點不畫圓點，但垂直線仍會畫出）。
    function yOfIndex(i) {
      var p = points[i];
      if (paceExtent && typeof p.pace_sec_per_km === 'number' && isFinite(p.pace_sec_per_km)) {
        var clamped = Math.min(Math.max(p.pace_sec_per_km, paceExtent.min), paceExtent.max);
        var ratio = (paceExtent.max - clamped) / (paceExtent.max - paceExtent.min);
        return box.top + box.height - ratio * box.height;
      }
      if (hrExtent && typeof p.hr_bpm === 'number' && isFinite(p.hr_bpm)) {
        var r = (p.hr_bpm - hrExtent.min) / (hrExtent.max - hrExtent.min);
        return box.top + box.height - r * box.height;
      }
      return null;
    }
    attachContinuousHitLayer(svg, box, buildContinuousHitMeta(box, xs, logicalXs, yOfIndex, tips));
    return svg;
  }

  /**
   * 建立會在 null 處斷開的折線（缺值不補 0、不內插）。
   * @param {Array} items 資料點
   * @param {Function} xOf item → x
   * @param {Function} yOf item → y 或 null（null 代表此點缺值）
   */
  function buildBrokenPath(items, xOf, yOf, className) {
    var d = '';
    var penDown = false;
    items.forEach(function (item) {
      var y = yOf(item);
      if (y === null || y === undefined || !isFinite(y)) {
        penDown = false; // 遇到缺值 → 抬筆，形成斷線
        return;
      }
      var x = xOf(item);
      d += (penDown ? ' L ' : ' M ') + x.toFixed(2) + ' ' + y.toFixed(2);
      penDown = true;
    });
    return el('path', { d: d.trim(), class: className, fill: 'none' });
  }

  // ---------------------------------------------------------------- 3. 心率區間分布

  /**
   * 心率區間分布（水平長條，顯示各區間秒數與佔比）。
   * 只畫 Z1~Z5：後端已濾掉低於 Z1 的暖身時間與裝置固定的尾端 0 padding。
   * @param {Array} zones [{zone, seconds, pct}] — pct 由後端算好（分母含暖身時間），
   *   前端不重算，避免分母不一致導致佔比虛增。
   * @param {Object} opts { containerWidth }
   */
  function hrZoneChart(zones, opts) {
    var options = opts || {};
    var height = Math.max(zones.length * 30 + 26, 90);
    var svg = createSvg(height, '心率區間分布長條圖');
    var box = { left: 46, top: 10, width: VB_WIDTH - 46 - 96, height: height - 10 - 16 };
    var maxSec = Math.max.apply(null, zones.map(function (z) { return z.seconds || 0; }).concat([1]));
    var rowH = box.height / Math.max(zones.length, 1);
    // 目前列數固定為 Z1~Z5（最多 5 列），尚無需依 containerWidth 抽稀；
    // 保留參數是為了與其他圖表統一簽名，供未來（如 Phase 3 tooltip）沿用同一介面。
    void options.containerWidth;

    var hitItems = [];
    zones.forEach(function (z, i) {
      var y = box.top + i * rowH;
      var barH = Math.min(rowH * 0.62, 20);
      var w = ((z.seconds || 0) / maxSec) * box.width;
      svg.appendChild(text('Z' + z.zone, {
        x: box.left - 8, y: y + rowH / 2 + 4, class: 'tick-label', 'text-anchor': 'end'
      }));
      svg.appendChild(el('rect', {
        x: box.left, y: y + (rowH - barH) / 2, width: box.width, height: barH, rx: 3, class: 'bar-track'
      }));
      svg.appendChild(el('rect', {
        x: box.left, y: y + (rowH - barH) / 2, width: Math.max(w, 0), height: barH, rx: 3,
        class: 'bar bar-zone bar-zone-' + z.zone
      }));
      var pct = z.pct || 0;
      svg.appendChild(text(formatDuration(z.seconds) + '（' + pct.toFixed(0) + '%）', {
        x: box.left + box.width + 8, y: y + rowH / 2 + 4, class: 'tick-label', 'text-anchor': 'start'
      }));
      hitItems.push({
        x: box.left, y: y, width: box.width + 96, height: rowH,
        tip: 'Z' + z.zone + ' ・ ' + formatDuration(z.seconds) + '（' + pct.toFixed(0) + '%）'
      });
    });
    attachDiscreteHitLayer(svg, hitItems);
    return svg;
  }

  // ---------------------------------------------------------------- 4. 跨場趨勢折線

  /**
   * 跨場次趨勢折線圖（X 軸為場次日期，缺值斷線）。
   * @param {Array} series [{date, value}]（value 可為 null → 斷線）
   * @param {Object} opts { label, formatter, invertY, containerWidth, markers }
   */
  function trendLineChart(series, opts) {
    var options = opts || {};
    var height = 200;
    var svg = createSvg(height, options.label || '趨勢折線圖');
    var box = { left: 54, top: 14, width: VB_WIDTH - 54 - 18, height: height - 14 - 38 };
    if (!series || series.length === 0) return svg;

    var values = series.map(function (d) { return d.value; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    if (values.length === 0) {
      drawAxes(svg, box);
      return svg;
    }
    var extent = niceExtent(values, 0.14);
    var formatter = options.formatter || function (v) { return formatNumber(v, 0); };

    drawAxes(svg, box);
    drawYTicks(svg, box,
      options.invertY ? { min: extent.max, max: extent.min } : extent,
      formatter, 4);

    var step = series.length > 1 ? box.width / (series.length - 1) : 0;
    function xOf(_, i) { return box.left + (series.length > 1 ? i * step : box.width / 2); }
    function yOf(item) {
      if (typeof item.value !== 'number' || !isFinite(item.value)) return null;
      var r = options.invertY
        ? (extent.max - item.value) / (extent.max - extent.min)
        : (item.value - extent.min) / (extent.max - extent.min);
      return box.top + box.height - r * box.height;
    }

    // 折線（缺值斷開）
    var d = '';
    var penDown = false;
    series.forEach(function (item, i) {
      var y = yOf(item);
      if (y === null) { penDown = false; return; }
      d += (penDown ? ' L ' : ' M ') + xOf(item, i).toFixed(2) + ' ' + y.toFixed(2);
      penDown = true;
    });
    svg.appendChild(el('path', { d: d.trim(), class: 'line ' + (options.lineClass || 'line-primary'), fill: 'none' }));

    // 資料點（點數不多時才畫，避免密集重疊）
    if (series.length <= 90) {
      series.forEach(function (item, i) {
        var y = yOf(item);
        if (y === null) return;
        var dot = el('circle', { cx: xOf(item, i), cy: y, r: series.length > 45 ? 1.8 : 2.8, class: 'dot ' + (options.dotClass || 'dot-primary') });
        var title = el('title');
        title.textContent = shortDate(item.date) + '：' + formatter(item.value);
        dot.appendChild(title);
        svg.appendChild(dot);
      });
    }

    // X 軸標籤：依容器寬度抽稀
    var tickIdx = pickTickIndexes(series.length, maxTickCount(options.containerWidth, 60));
    tickIdx.forEach(function (i) {
      svg.appendChild(text(shortDate(series[i].date), {
        x: xOf(series[i], i), y: box.top + box.height + 17, class: 'tick-label', 'text-anchor': 'middle'
      }));
    });

    // 連續模式：range=1y/all 時 series 可能遠超過 90 筆（見上方 circle 的
    // 條件式判斷），此時完全沒有 per-point 元素可 hover，故一律額外提供
    // 命中 meta。logicalXs 用完整 ISO 日期字串（跨場趨勢群組的同步基準）
    // ——字典序比較與時間序一致，可直接拿來在同群組其他圖（週跑量長條圖）
    // 的 logicalXs 陣列做二分搜尋。
    //
    // tips 用 {label, value} 結構（而非純字串）：app.js 的同步游標標籤會把
    // label（補零日期 MM/DD）用正常字重、value（數值）用粗體分開渲染，
    // 讓數字在小標籤裡更醒目；label 補零對齊也比不補零讀起來整齊。
    var xsAll = series.map(xOf);
    var logicalXsAll = series.map(function (item) { return item.date; });
    var tipsAll = series.map(function (item) {
      return {
        label: shortDatePadded(item.date),
        value: typeof item.value === 'number' && isFinite(item.value) ? formatter(item.value) : '無資料'
      };
    });
    function yOfIndexTrend(i) { return yOf(series[i]); }
    attachContinuousHitLayer(svg, box, buildContinuousHitMeta(box, xsAll, logicalXsAll, yOfIndexTrend, tipsAll));
    return svg;
  }

  // ---------------------------------------------------------------- 5. 通用長條圖（週跑量）

  /**
   * 通用垂直長條圖，用於週跑量。
   * @param {Array} bars [{label, value, weekStart}]（weekStart 為選填的
   *   ISO 週一日期，用於跨圖同步游標時定位「滑鼠所在日期屬於哪一週」）
   */
  function barChart(bars, opts) {
    var options = opts || {};
    var height = 200;
    var svg = createSvg(height, options.label || '長條圖');
    var box = { left: 54, top: 14, width: VB_WIDTH - 54 - 18, height: height - 14 - 38 };
    if (!bars || bars.length === 0) return svg;

    var values = bars.map(function (b) { return b.value; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    var max = values.length ? Math.max.apply(null, values) : 1;
    var extent = { min: 0, max: max * 1.15 || 1 };
    var formatter = options.formatter || function (v) { return formatNumber(v, 0); };

    drawAxes(svg, box);
    drawYTicks(svg, box, extent, formatter, 4);

    var slot = box.width / bars.length;
    var barWidth = Math.max(Math.min(slot * 0.66, 46), 3);
    var tickIdx = pickTickIndexes(bars.length, maxTickCount(options.containerWidth, 68));
    var hitItems = [];
    var centers = [];

    bars.forEach(function (b, i) {
      var cx = box.left + slot * (i + 0.5);
      centers.push(cx);
      var hasValue = typeof b.value === 'number' && isFinite(b.value);
      var barTop = box.top + box.height;
      if (hasValue) {
        var h = (b.value / extent.max) * box.height;
        barTop = box.top + box.height - h;
        var rect = el('rect', {
          x: cx - barWidth / 2, y: barTop,
          width: barWidth, height: Math.max(h, 0), rx: 2,
          class: 'bar ' + (options.barClass || 'bar-volume')
        });
        var title = el('title');
        title.textContent = b.label + '：' + formatter(b.value);
        rect.appendChild(title);
        svg.appendChild(rect);
      }
      if (tickIdx.indexOf(i) !== -1) {
        svg.appendChild(text(b.label, {
          x: cx, y: box.top + box.height + 17, class: 'tick-label', 'text-anchor': 'middle'
        }));
      }
      if (hasValue) {
        hitItems.push({
          x: cx - slot / 2, y: box.top, width: slot, height: box.height,
          tip: b.label + '：' + formatter(b.value)
        });
      }
    });
    attachDiscreteHitLayer(svg, hitItems);

    // 若呼叫端提供 weekStart，額外附加被動同步 meta（不畫覆蓋矩形，保留
    // 上面逐週的離散 hover）——同一群組（跨場趨勢）的其他圖以「日期」為
    // 邏輯 X，這裡用每個 bar 所屬週的 weekStart 當邏輯 X，讓游標移到任一
    // 天時能定位到對應週。
    var hasWeekStart = bars.every(function (b) { return !!b.weekStart; });
    if (hasWeekStart) {
      var logicalXs = bars.map(function (b) { return b.weekStart; });
      function yOfBar(i) {
        var b = bars[i];
        if (typeof b.value !== 'number' || !isFinite(b.value)) return null;
        var h = (b.value / extent.max) * box.height;
        return box.top + box.height - h;
      }
      var tips = bars.map(function (b) {
        return b.label + '：' + (typeof b.value === 'number' && isFinite(b.value) ? formatter(b.value) : '無資料');
      });
      attachSyncMeta(svg, buildContinuousHitMeta(box, centers, logicalXs, yOfBar, tips));
    }
    return svg;
  }

  // ---------------------------------------------------------------- 6. 身體狀況折線（含訓練日標記）

  /**
   * 每日身體狀況折線圖，X 軸依日期等距排列，並在訓練日加標記。
   * @param {Array} points [{date, value}]（只含有值的日期）
   * @param {Object} opts { startDate, endDate, trainingDays:Set|Array, formatter, containerWidth, label }
   */
  function wellnessChart(points, opts) {
    var options = opts || {};
    // 此圖常位於較窄的右欄，viewBox 稍矮一點以免縮放後字太小
    var height = 200;
    var svg = createSvg(height, options.label || '每日身體狀況折線圖');
    var box = { left: 56, top: 12, width: VB_WIDTH - 56 - 20, height: height - 12 - 48 };
    if (!points || points.length === 0) return svg;

    // 以整段日期區間為 X 軸（缺值日期會自然形成斷點）
    var start = options.startDate || points[0].date;
    var end = options.endDate || points[points.length - 1].date;
    var startMs = Date.parse(start + 'T00:00:00Z');
    var endMs = Date.parse(end + 'T00:00:00Z');
    var spanMs = Math.max(endMs - startMs, 86400000);
    var dayMs = 86400000;

    var values = points.map(function (p) { return p.value; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    if (values.length === 0) return svg;
    var extent = niceExtent(values, 0.16);
    var formatter = options.formatter || function (v) { return formatNumber(v, 0); };

    drawAxes(svg, box);
    drawYTicks(svg, box, extent, formatter, 3);

    function xOfDate(iso) {
      var ms = Date.parse(iso + 'T00:00:00Z');
      return box.left + ((ms - startMs) / spanMs) * box.width;
    }
    function yOf(v) {
      return box.top + box.height - ((v - extent.min) / (extent.max - extent.min)) * box.height;
    }

    // 訓練日標記：整欄淡色背景（取代原本的 X 軸短豎線）。原本的短豎線
    // 太不顯眼，容易被誤以為「只有訓練日才有 X 軸標籤」；改成整欄背景色
    // 後，訓練日與非訓練日的視覺區隔在繪圖區內就看得出來，不需 hover。
    // 必須在折線/資料點之前畫（先畫的在下層），否則色帶會蓋住折線。
    var dayWidth = spanMs > 0 ? (dayMs / spanMs) * box.width : box.width;
    var trainingDays = options.trainingDays || [];
    var tdList = trainingDays instanceof Set ? Array.from(trainingDays) : trainingDays;
    tdList.forEach(function (day) {
      if (day < start || day > end) return;
      var cx = xOfDate(day);
      var band = el('rect', {
        x: cx - dayWidth / 2, y: box.top, width: dayWidth, height: box.height,
        class: 'training-day-band'
      });
      var title = el('title');
      title.textContent = shortDatePadded(day) + '：訓練日';
      band.appendChild(title);
      svg.appendChild(band);
    });

    // 折線：相鄰資料點若日期相差超過 1 天，視為缺值 → 斷線（不補 0、不內插）
    var d = '';
    var prevMs = null;
    points.forEach(function (p) {
      if (typeof p.value !== 'number' || !isFinite(p.value)) { prevMs = null; return; }
      var ms = Date.parse(p.date + 'T00:00:00Z');
      var contiguous = prevMs !== null && (ms - prevMs) <= dayMs * 1.5;
      d += (contiguous ? ' L ' : ' M ') + xOfDate(p.date).toFixed(2) + ' ' + yOf(p.value).toFixed(2);
      prevMs = ms;
    });
    svg.appendChild(el('path', { d: d.trim(), class: 'line ' + (options.lineClass || 'line-primary'), fill: 'none' }));

    if (points.length <= 120) {
      points.forEach(function (p) {
        if (typeof p.value !== 'number' || !isFinite(p.value)) return;
        var dot = el('circle', {
          cx: xOfDate(p.date), cy: yOf(p.value),
          r: points.length > 60 ? 1.5 : 2.4,
          class: 'dot ' + (options.dotClass || 'dot-primary')
        });
        var title = el('title');
        title.textContent = shortDate(p.date) + '：' + formatter(p.value);
        dot.appendChild(title);
        svg.appendChild(dot);
      });
    }

    // X 軸標籤：依整段日期均分，並依容器寬度抽稀
    var totalDays = Math.round(spanMs / dayMs) + 1;
    var labelIdx = pickTickIndexes(totalDays, maxTickCount(options.containerWidth, 58));
    labelIdx.forEach(function (i) {
      var ms = startMs + i * dayMs;
      var iso = new Date(ms).toISOString().slice(0, 10);
      svg.appendChild(text(shortDate(iso), {
        x: box.left + ((ms - startMs) / spanMs) * box.width,
        y: box.top + box.height + 26,
        class: 'tick-label', 'text-anchor': 'middle'
      }));
    });

    // 連續模式：range=1y/all 時 points 可能遠超過 120 筆（見上方 circle 的
    // 條件式判斷），此時完全沒有 per-point 元素可 hover。只用有值的日期
    // 建 xs/tips（points 本身已只含有值日期），缺值日期不會被反查命中。
    var valuePoints = points.filter(function (p) {
      return typeof p.value === 'number' && isFinite(p.value);
    });
    if (valuePoints.length > 0) {
      var xsPts = valuePoints.map(function (p) { return xOfDate(p.date); });
      // logicalXs 用完整 ISO 日期字串（同步比對基準，字典序＝時間序）；
      // tips 用 {label, value} 結構，label 補零日期正常字重、value 數值
      // 粗體，由 app.js 的同步游標標籤分開渲染（見 trendLineChart 同款設計）。
      var logicalXsPts = valuePoints.map(function (p) { return p.date; });
      var tipsPts = valuePoints.map(function (p) {
        return { label: shortDatePadded(p.date), value: formatter(p.value) };
      });
      function yOfIndexWellness(i) { return yOf(valuePoints[i].value); }
      attachContinuousHitLayer(svg, box, buildContinuousHitMeta(box, xsPts, logicalXsPts, yOfIndexWellness, tipsPts));
    }
    return svg;
  }

  // ---------------------------------------------------------------- 對外介面

  return {
    lapPaceChart: lapPaceChart,
    recordsChart: recordsChart,
    hrZoneChart: hrZoneChart,
    trendLineChart: trendLineChart,
    barChart: barChart,
    wellnessChart: wellnessChart,
    emptyBlock: emptyBlock,
    // 格式化工具（app.js 也會用到，集中一份避免重複實作）
    formatPace: formatPace,
    formatDuration: formatDuration,
    formatNumber: formatNumber,
    shortDate: shortDate
  };
})();
