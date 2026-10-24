/**
 * route-map.js — 單場路線地圖（N-32）
 *
 * 路線來自 /api/sessions/{id} 的 records.points（每 10 秒一點，含 lat/lon），
 * 與配速／心率曲線共用同一份資料，所以滑過曲線時能在地圖上標出同一個時間點。
 *
 * 底圖：Leaflet + OpenStreetMap 官方圖磚，不需要 API key（Google／Apple 地圖都要
 * key 與帳單帳戶）。CARTO basemaps 原本免 key，2026-10-24 實測已改成要 key，不能用。
 * 深色主題沒有免 key 的深色圖磚，改用 CSS 濾鏡把圖磚反轉（styles.css .route-map-dark）。
 * 載不到 Leaflet（沒網路）時退回純 SVG，只畫路線形狀，不畫底圖。
 *
 * 路線分段上色用「本場」配速或心率的五分位，只呈現同一場裡的相對快慢，
 * 不代表訓練區間。
 */

import { h } from './core/dom.js';
import { formatDuration, formatPace } from './charts.js';

var LEAFLET_JS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';
var LEAFLET_CSS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';
var LOAD_TIMEOUT_MS = 8000;
var TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
var TILE_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

// 五段顏色沿用心率區間的色票（冷 → 暖），深淺色主題各自定義在 styles.css
var BUCKET_VARS = ['--c-zone-1', '--c-zone-2', '--c-zone-3', '--c-zone-4', '--c-zone-5'];
var MODES = [
  { key: 'pace', label: '配速' },
  { key: 'hr', label: '心率' }
];
var colorMode = 'pace';

var leafletPromise = null;
// 已建立的 Leaflet 地圖；容器被換掉後要 remove()，否則事件與圖磚請求會殘留
var liveMaps = [];

/** 逐點資料是否帶座標（後端整場沒有 GPS 時不附 lat/lon 欄位） */
export function hasRoute(detail) {
  var pts = detail && detail.records && detail.records.points;
  return Array.isArray(pts) && pts.some(function (p) { return typeof p.lat === 'number'; });
}

function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise(function (resolve, reject) {
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = LEAFLET_CSS;
    document.head.appendChild(css);
    var script = document.createElement('script');
    script.src = LEAFLET_JS;
    var timer = setTimeout(function () { reject(new Error('timeout')); }, LOAD_TIMEOUT_MS);
    script.onload = function () { clearTimeout(timer); window.L ? resolve(window.L) : reject(new Error('no L')); };
    script.onerror = function () { clearTimeout(timer); reject(new Error('load failed')); };
    document.head.appendChild(script);
  });
  // 失敗後允許下次重試（例如手機剛連上網路）
  leafletPromise.catch(function () { leafletPromise = null; });
  return leafletPromise;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888';
}

function isDark() {
  return document.documentElement.getAttribute('data-theme') === 'dark';
}

/** 依本場數值的五分位切出 4 個門檻；數值越「好」（配速越快、心率越高）桶號越大 */
function bucketFn(points, mode) {
  var key = mode === 'hr' ? 'hr_bpm' : 'pace_sec_per_km';
  var vals = points.map(function (p) { return p[key]; })
    .filter(function (v) { return typeof v === 'number' && isFinite(v) && v > 0; })
    .sort(function (a, b) { return a - b; });
  if (vals.length === 0) return function () { return null; };
  var cuts = [0.2, 0.4, 0.6, 0.8].map(function (q) { return vals[Math.floor(vals.length * q)]; });
  return function (p) {
    var v = p[key];
    if (typeof v !== 'number' || !isFinite(v) || v <= 0) return null;
    var b = 0;
    cuts.forEach(function (c) { if (v > c) b += 1; });
    return mode === 'hr' ? b : 4 - b; // 配速秒數越小越快
  };
}

/**
 * 把路線切成同色的連續段：[{bucket, coords: [[lat, lon], ...]}]。
 * 缺座標的點（GPS 中斷）斷開，不跨洞連線。
 */
function segments(points, mode) {
  var bucketOf = bucketFn(points, mode);
  var out = [];
  var cur = null;
  var prev = null;
  points.forEach(function (p) {
    if (typeof p.lat !== 'number' || typeof p.lon !== 'number') { cur = null; prev = null; return; }
    var b = bucketOf(p);
    var ll = [p.lat, p.lon];
    if (!cur || cur.bucket !== b) {
      cur = { bucket: b, coords: prev ? [prev] : [] };
      out.push(cur);
    }
    cur.coords.push(ll);
    prev = ll;
  });
  return out;
}

function located(points) {
  return points.filter(function (p) { return typeof p.lat === 'number' && typeof p.lon === 'number'; });
}

/** 依經過時間找最近的有座標點（points 已依 elapsed_sec 排序） */
function nearestByElapsed(points, elapsed) {
  var lo = 0;
  var hi = points.length - 1;
  while (lo < hi) {
    var mid = (lo + hi) >> 1;
    if (points[mid].elapsed_sec < elapsed) lo = mid + 1; else hi = mid;
  }
  if (lo > 0 && Math.abs(points[lo - 1].elapsed_sec - elapsed) < Math.abs(points[lo].elapsed_sec - elapsed)) {
    lo -= 1;
  }
  return points[lo];
}

function pointLabel(p) {
  var parts = [formatDuration(p.elapsed_sec)];
  if (typeof p.pace_sec_per_km === 'number') parts.push(formatPace(p.pace_sec_per_km) + '/km');
  if (typeof p.hr_bpm === 'number') parts.push(p.hr_bpm + ' bpm');
  return parts.join(' ・ ');
}

function pruneMaps() {
  liveMaps = liveMaps.filter(function (m) {
    if (m.getContainer().isConnected) return true;
    m.remove();
    return false;
  });
}

// ------------------------------------------------------------- Leaflet 版

function drawLeaflet(L, box, points, mode) {
  pruneMaps();
  var map = L.map(box, { scrollWheelZoom: false, attributionControl: true, zoomSnap: 0.25 });
  liveMaps.push(map);
  box.classList.toggle('route-map-dark', isDark());
  L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION }).addTo(map);

  var colors = BUCKET_VARS.map(cssVar);
  var all = [];
  segments(points, mode).forEach(function (seg) {
    all = all.concat(seg.coords);
    L.polyline(seg.coords, {
      color: seg.bucket === null ? cssVar('--c-axis') : colors[seg.bucket],
      weight: 4,
      opacity: 0.95,
      lineCap: 'round',
      lineJoin: 'round'
    }).addTo(map);
  });
  map.fitBounds(L.latLngBounds(all), { padding: [16, 16] });

  var pts = located(points);
  var ends = [pts[0], pts[pts.length - 1]];
  ends.forEach(function (p, i) {
    L.circleMarker([p.lat, p.lon], {
      radius: 5, weight: 2, color: '#fff', fillOpacity: 1,
      fillColor: i === 0 ? cssVar('--c-pace') : cssVar('--text')
    }).addTo(map).bindTooltip(i === 0 ? '起點' : '終點');
  });

  var cursor = L.circleMarker([pts[0].lat, pts[0].lon], {
    radius: 7, weight: 3, color: '#fff', fillColor: cssVar('--accent'), fillOpacity: 1
  });
  return {
    show: function (p) {
      cursor.setLatLng([p.lat, p.lon]);
      if (!map.hasLayer(cursor)) cursor.addTo(map);
      cursor.bindTooltip(pointLabel(p), { direction: 'top', offset: [0, -8] }).openTooltip();
    },
    hide: function () { if (map.hasLayer(cursor)) map.removeLayer(cursor); }
  };
}

// ------------------------------------------------------------- 離線 SVG 版

var SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs) {
  var node = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
  return node;
}

function drawSvg(box, points, mode) {
  var pts = located(points);
  var midLat = pts.reduce(function (s, p) { return s + p.lat; }, 0) / pts.length;
  var kx = Math.cos(midLat * Math.PI / 180); // 經度在此緯度的縮短比例
  var xs = pts.map(function (p) { return p.lon * kx; });
  var ys = pts.map(function (p) { return p.lat; });
  var minX = Math.min.apply(null, xs);
  var maxX = Math.max.apply(null, xs);
  var minY = Math.min.apply(null, ys);
  var maxY = Math.max.apply(null, ys);
  var W = 600;
  var H = 260;
  var pad = 16;
  var span = Math.max(maxX - minX, maxY - minY, 1e-6);
  var s = Math.min((W - 2 * pad) / (maxX - minX || span), (H - 2 * pad) / (maxY - minY || span));
  var ox = (W - (maxX - minX) * s) / 2;
  var oy = (H - (maxY - minY) * s) / 2;
  function proj(ll) {
    return [ox + (ll[1] * kx - minX) * s, H - (oy + (ll[0] - minY) * s)];
  }

  var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'route-svg', role: 'img', 'aria-label': '路線形狀' });
  var colors = BUCKET_VARS.map(cssVar);
  segments(points, mode).forEach(function (seg) {
    var d = seg.coords.map(function (ll, i) {
      var xy = proj(ll);
      return (i ? 'L' : 'M') + xy[0].toFixed(1) + ' ' + xy[1].toFixed(1);
    }).join(' ');
    svg.appendChild(svgEl('path', {
      d: d, fill: 'none', 'stroke-width': 4, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      stroke: seg.bucket === null ? cssVar('--c-axis') : colors[seg.bucket]
    }));
  });
  var cursor = svgEl('circle', { r: 7, fill: cssVar('--accent'), stroke: '#fff', 'stroke-width': 3, visibility: 'hidden' });
  svg.appendChild(cursor);
  box.appendChild(svg);
  var label = h('div', 'route-cursor-label');
  box.appendChild(label);
  return {
    show: function (p) {
      var xy = proj([p.lat, p.lon]);
      cursor.setAttribute('cx', xy[0]);
      cursor.setAttribute('cy', xy[1]);
      cursor.setAttribute('visibility', 'visible');
      label.textContent = pointLabel(p);
    },
    hide: function () { cursor.setAttribute('visibility', 'hidden'); label.textContent = ''; }
  };
}

// ------------------------------------------------------------- 對外

/**
 * 在 node 內畫路線地圖，回傳 { show(elapsed_sec), hide() } 供曲線圖連動。
 * 地圖非同步載入，載入完成前呼叫 show/hide 會被忽略。
 */
export function renderRouteMap(node, detail) {
  var points = (detail.records && detail.records.points) || [];
  var pts = located(points);
  var handle = null;
  var api = {
    show: function (elapsed) { if (handle) handle.show(nearestByElapsed(pts, elapsed)); },
    hide: function () { if (handle) handle.hide(); }
  };
  if (pts.length < 2) return api;

  var head = h('div', 'route-head');
  var toggle = h('div', 'route-mode');
  toggle.setAttribute('role', 'group');
  toggle.setAttribute('aria-label', '路線顏色依據');
  MODES.forEach(function (m) {
    var btn = h('button', 'route-mode-btn' + (m.key === colorMode ? ' is-active' : ''), m.label);
    btn.type = 'button';
    btn.setAttribute('aria-pressed', m.key === colorMode ? 'true' : 'false');
    btn.addEventListener('click', function () {
      if (colorMode === m.key) return;
      colorMode = m.key;
      Array.prototype.forEach.call(toggle.children, function (b, i) {
        var on = MODES[i].key === colorMode;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      legendText.textContent = legendLabel();
      draw();
    });
    toggle.appendChild(btn);
  });
  head.appendChild(toggle);
  var legend = h('div', 'route-legend');
  var legendText = h('span', null, legendLabel());
  var ramp = h('span', 'route-ramp');
  BUCKET_VARS.forEach(function (v) {
    var sw = h('span', 'route-ramp-step');
    sw.style.background = 'var(' + v + ')';
    ramp.appendChild(sw);
  });
  legend.appendChild(legendText);
  legend.appendChild(ramp);
  head.appendChild(legend);
  node.appendChild(head);

  var box = h('div', 'route-map');
  node.appendChild(box);
  var note = h('div', 'chart-note');
  node.appendChild(note);

  function legendLabel() {
    return colorMode === 'hr' ? '本場心率五分位：低 → 高' : '本場配速五分位：慢 → 快';
  }

  function draw() {
    box.replaceChildren();
    box.classList.remove('route-map-offline');
    handle = null;
    loadLeaflet().then(function (L) {
      if (!box.isConnected) return;
      box.replaceChildren();
      handle = drawLeaflet(L, box, points, colorMode);
      note.textContent = '';
    }).catch(function () {
      if (!box.isConnected) return;
      box.replaceChildren();
      box.classList.add('route-map-offline');
      handle = drawSvg(box, points, colorMode);
      note.textContent = '無法載入地圖底圖（可能沒有網路），只顯示路線形狀。';
    });
  }
  draw();
  return api;
}

/**
 * 讓逐秒曲線圖（charts.recordsChart）的游標帶動地圖上的位置標記。
 * 讀曲線圖掛在 svg 上的 __syncMeta（xs 是 SVG 座標、logicalXs 是經過秒數）。
 */
export function linkChartToMap(chartNode, mapApi) {
  var svg = chartNode && chartNode.querySelector('svg');
  if (!svg || !svg.__syncMeta) return;
  var meta = svg.__syncMeta;
  function onMove(evt) {
    var ctm = svg.getScreenCTM();
    if (!ctm) return;
    var pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    var x = pt.matrixTransform(ctm.inverse()).x;
    if (x < meta.box.left || x > meta.box.left + meta.box.width) { mapApi.hide(); return; }
    var lo = 0;
    var hi = meta.xs.length - 1;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (meta.xs[mid] < x) lo = mid + 1; else hi = mid;
    }
    mapApi.show(meta.logicalXs[lo]);
  }
  svg.addEventListener('pointermove', onMove);
  svg.addEventListener('pointerdown', onMove);
  // 觸控 tap 結束後瀏覽器會緊接著送 pointerleave（見 core/tooltip.js），
  // 觸控時不收起標記，否則點一下就看不到位置
  svg.addEventListener('pointerleave', function (evt) {
    if (evt.pointerType !== 'touch') mapApi.hide();
  });
}
