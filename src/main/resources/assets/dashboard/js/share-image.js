/**
 * share-image.js — 單場分享圖（透明底 PNG，可疊在照片或 IG 限動上）
 *
 * 版型參考 Strava 的分享貼圖，兩種：
 *   route  — 路線疊在置中的數據欄上（要有 GPS）
 *   splits — 每公里分段長條（要有分圈），選了數據欄位就排在下方
 * 畫布固定 1080×1920（IG 限動 9:16），背景不填色，toBlob 輸出即為透明 PNG。
 *
 * 路線／長條顏色可選單色，或依本場配速／心率五分位上色（同 route-map.js 的
 * 地圖分段，只呈現同一場裡的相對快慢，不代表訓練區間）。
 * 使用者的選擇（版型、顏色、欄位勾選與順序）存在 Theme 設定的 shareImage 欄位。
 */

import { h } from './core/dom.js';
import { WORKOUT_TYPE_LABEL } from './core/state.js';
import { formatDuration, formatNumber, formatPace } from './charts.js';
import { BUCKET_VARS, bucketFn, cssVar, hasRoute, located, projector, segments } from './route-map.js';
import * as Theme from './theme.js';

var W = 1080;
var H = 1920;

var TEMPLATES = [
  { key: 'route', label: '路線＋數據' },
  { key: 'splits', label: '每公里分段' }
];

// 單色選項；pace／hr 為依本場快慢分段上色
var LINE_COLORS = [
  { key: '#FC5200', label: '橘' },
  { key: '#E5484D', label: '紅' },
  { key: '#F5C400', label: '黃' },
  { key: '#2BB673', label: '綠' },
  { key: '#2F80ED', label: '藍' },
  { key: '#FFFFFF', label: '白' },
  { key: '#111111', label: '黑' },
  { key: 'pace', label: '依配速' },
  { key: 'hr', label: '依心率' }
];

var TEXT_COLORS = [
  { key: '#FFFFFF', label: '白字' },
  { key: '#111111', label: '黑字' }
];

/**
 * 可勾選的欄位。kind=meta 是表頭文字（日期、課種、標題），其餘是「標籤＋數值」。
 * value 回傳 null 代表本場沒有這項資料，畫圖時略過、勾選清單標示無資料。
 */
var FIELDS = [
  { key: 'date', label: '日期', kind: 'meta', value: function (d) {
    return d.started_at ? d.started_at.slice(0, 10).replace(/-/g, '/') : null;
  } },
  { key: 'type', label: '課種', kind: 'meta', value: function (d) {
    return d.workout_type ? (WORKOUT_TYPE_LABEL[d.workout_type] || d.workout_type) : null;
  } },
  { key: 'title', label: '標題', kind: 'meta', value: function (d) { return d.title || null; } },
  { key: 'distance', label: '距離', value: function (d, s) { return withUnit(formatNumber(s.distance_km, 2), ' 公里'); } },
  { key: 'pace', label: '配速', value: function (d, s) { return withUnit(formatPace(s.avg_pace_sec_per_km), ' /公里'); } },
  { key: 'time', label: '時間', value: function (d, s) { return withUnit(formatDuration(s.duration_sec), ''); } },
  { key: 'avg_hr', label: '平均心率', value: function (d, s) { return withUnit(formatNumber(s.avg_hr_bpm, 0), ' bpm'); } },
  { key: 'max_hr', label: '最大心率', value: function (d, s) { return withUnit(formatNumber(s.max_hr_bpm, 0), ' bpm'); } },
  { key: 'cadence', label: '平均步頻', value: function (d, s) { return withUnit(formatNumber(s.avg_cadence_spm, 0), ' spm'); } },
  { key: 'elevation', label: '爬升', value: function (d, s) { return withUnit(formatNumber(s.elevation_gain_m, 0), ' 公尺'); } },
  { key: 'calories', label: '熱量', value: function (d, s) {
    return typeof s.calories === 'number' ? Math.round(s.calories).toLocaleString('en-US') + ' 大卡' : null;
  } },
  { key: 'aerobic_te', label: '有氧 TE', value: function (d, s) { return withUnit(formatNumber(s.aerobic_te, 1), ''); } }
];

var DEFAULT_PREFS = {
  template: 'route',
  line: '#FC5200',
  text: '#FFFFFF',
  order: FIELDS.map(function (f) { return f.key; }),
  checked: ['distance', 'pace', 'time', 'elevation', 'calories', 'avg_hr']
};

function withUnit(text, unit) {
  return text === '—' ? null : text + unit;
}

function fieldDef(key) {
  return FIELDS.filter(function (f) { return f.key === key; })[0];
}

/** 讀偏好；欄位清單日後增減時，順序補上新欄位、丟掉不認得的 */
function loadPrefs() {
  var saved = Theme.getSettings().shareImage || {};
  var prefs = {};
  Object.keys(DEFAULT_PREFS).forEach(function (k) {
    prefs[k] = saved[k] !== undefined ? saved[k] : DEFAULT_PREFS[k];
  });
  var known = FIELDS.map(function (f) { return f.key; });
  prefs.order = prefs.order.filter(function (k) { return known.indexOf(k) !== -1; });
  known.forEach(function (k) { if (prefs.order.indexOf(k) === -1) prefs.order.push(k); });
  prefs.checked = prefs.checked.filter(function (k) { return known.indexOf(k) !== -1; });
  return prefs;
}

function savePrefs(prefs) {
  Theme.saveSettings({ shareImage: prefs });
}

function hasLaps(detail) {
  return !!(detail.laps && detail.laps.available !== false && (detail.laps.laps || []).length > 0);
}

/** 本場能做分享圖嗎（路線與分圈都沒有就整塊收起） */
export function canShare(detail) {
  return hasRoute(detail) || hasLaps(detail);
}

// ------------------------------------------------------------- 繪圖

function fontFamily() {
  return getComputedStyle(document.body).fontFamily;
}

function setFont(ctx, weight, size) {
  ctx.font = weight + ' ' + size + 'px ' + fontFamily();
}

function bucketColors() {
  return BUCKET_VARS.map(cssVar);
}

/** 太長的文字截斷加刪節號 */
function fitText(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  var t = text;
  while (t.length > 1 && ctx.measureText(t + '…').width > maxWidth) t = t.slice(0, -1);
  return t + '…';
}

function selectedFields(detail, prefs, kind) {
  var s = detail.summary || {};
  return prefs.order
    .filter(function (k) { return prefs.checked.indexOf(k) !== -1; })
    .map(fieldDef)
    .filter(function (f) { return (f.kind || 'stat') === kind; })
    .map(function (f) { return { label: f.label, value: f.value(detail, s) }; })
    .filter(function (f) { return f.value !== null; });
}

/** 表頭（日期・課種／標題），回傳占用高度；measure=true 時只量不畫 */
function drawMeta(ctx, metas, x, y, align, color, measure) {
  if (metas.length === 0) return 0;
  var small = metas.filter(function (m) { return m.label !== '標題'; }).map(function (m) { return m.value; });
  var title = metas.filter(function (m) { return m.label === '標題'; })[0];
  var used = 0;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = 'top';
  if (small.length) {
    setFont(ctx, 600, 34);
    if (!measure) ctx.fillText(small.join('・'), x, y);
    used += 50;
  }
  if (title) {
    setFont(ctx, 700, 48);
    if (!measure) ctx.fillText(fitText(ctx, title.value, 900), x, y + used);
    used += 68;
  }
  return used + 40;
}

/** 標籤＋數值格；cols 欄，每格中心對齊 */
function statGrid(stats, cols, labelSize, valueSize) {
  var rowH = labelSize + valueSize + 64;
  return { cols: cols, rowH: rowH, height: Math.ceil(stats.length / cols) * rowH, labelSize: labelSize, valueSize: valueSize };
}

function drawStatGrid(ctx, stats, grid, left, width, top, color) {
  var colW = width / grid.cols;
  ctx.fillStyle = color;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  stats.forEach(function (st, i) {
    var cx = left + colW * (i % grid.cols) + colW / 2;
    var y = top + Math.floor(i / grid.cols) * grid.rowH;
    setFont(ctx, 600, grid.labelSize);
    ctx.fillText(st.label, cx, y);
    setFont(ctx, 800, grid.valueSize);
    ctx.fillText(fitText(ctx, st.value, colW - 24), cx, y + grid.labelSize + 14);
  });
}

/** 分圈依本場配速／心率五分位分桶；逐點資料太密（每 10 秒），分享圖改以整公里上色才不會花 */
function lapBucketFn(laps, mode) {
  return bucketFn(laps.map(function (l) {
    return { pace_sec_per_km: l.pace_sec_per_km, hr_bpm: l.avg_hr_bpm };
  }), mode);
}

/** 逐點依所在公里數對應到分圈的分桶；沒有分圈時退回逐點分桶 */
function pointBucketByLap(detail, mode) {
  if (!hasLaps(detail)) return null;
  var laps = detail.laps.laps;
  var lapBucket = lapBucketFn(laps, mode);
  var ends = [];
  laps.reduce(function (acc, l) { ends.push(acc + (l.distance_km || 0)); return ends[ends.length - 1]; }, 0);
  return function (p) {
    if (typeof p.distance_km !== 'number') return null;
    var i = 0;
    while (i < laps.length - 1 && p.distance_km > ends[i]) i += 1;
    return lapBucket({ pace_sec_per_km: laps[i].pace_sec_per_km, hr_bpm: laps[i].avg_hr_bpm });
  };
}

function drawRoute(ctx, detail, box, line, width) {
  var points = (detail.records && detail.records.points) || [];
  var proj = projector(located(points), box.w, box.h, width);
  var gradient = line === 'pace' || line === 'hr';
  var colors = bucketColors();
  ctx.lineWidth = width;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  var mode = gradient ? line : 'pace';
  segments(points, mode, gradient ? pointBucketByLap(detail, mode) : null).forEach(function (seg) {
    if (seg.coords.length < 2) return;
    ctx.strokeStyle = !gradient ? line : (seg.bucket === null ? colors[2] : colors[seg.bucket]);
    ctx.beginPath();
    seg.coords.forEach(function (ll, i) {
      var xy = proj(ll);
      if (i) ctx.lineTo(box.x + xy[0], box.y + xy[1]); else ctx.moveTo(box.x + xy[0], box.y + xy[1]);
    });
    ctx.stroke();
  });
}

/** 版型 route：路線在後、數據欄置中疊在上面 */
function drawRouteTemplate(ctx, detail, prefs) {
  drawRoute(ctx, detail, { x: 120, y: 360, w: W - 240, h: H - 720 }, prefs.line, 22);

  var metas = selectedFields(detail, prefs, 'meta');
  var stats = selectedFields(detail, prefs, 'stat');
  var grid = stats.length > 6 ? statGrid(stats, 2, 34, 64) : statGrid(stats, 1, 36, 76);
  var metaH = drawMeta(ctx, metas, 0, 0, 'center', prefs.text, true);
  var top = (H - metaH - grid.height) / 2;
  drawMeta(ctx, metas, W / 2, top, 'center', prefs.text, false);
  drawStatGrid(ctx, stats, grid, 120, W - 240, top + metaH, prefs.text);
}

/** 版型 splits：每公里分段長條，越快越長；末段不足 1 km 時標實際距離 */
function drawSplitsTemplate(ctx, detail, prefs) {
  var laps = detail.laps.laps;
  var metas = selectedFields(detail, prefs, 'meta');
  var stats = selectedFields(detail, prefs, 'stat');
  var grid = statGrid(stats, 3, 28, 48);
  var statsH = stats.length ? grid.height + 40 : 0;
  var metaH = drawMeta(ctx, metas, 0, 0, 'left', prefs.text, true);
  var headH = 110;
  var rowH = Math.max(28, Math.min(62, (H - 360 - metaH - headH - statsH) / laps.length));
  var top = (H - (metaH + headH + rowH * laps.length + statsH)) / 2;
  var left = 110;
  var right = W - 110;

  drawMeta(ctx, metas, left, top, 'left', prefs.text, false);
  ctx.fillStyle = prefs.text;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  setFont(ctx, 800, 56);
  ctx.fillText('每公里分段', left, top + metaH);

  var speeds = laps.map(function (l) { return l.pace_sec_per_km > 0 ? 1 / l.pace_sec_per_km : 0; });
  var valid = speeds.filter(function (v) { return v > 0; });
  var minS = Math.min.apply(null, valid);
  var maxS = Math.max.apply(null, valid);
  var gradient = prefs.line === 'pace' || prefs.line === 'hr';
  var colors = bucketColors();
  var bucketOf = lapBucketFn(laps, gradient ? prefs.line : 'pace');

  var barLeft = left + 120;
  var barMax = right - 130 - barLeft;
  var fontSize = Math.round(rowH * 0.5);
  var thick = Math.round(rowH * 0.48);
  laps.forEach(function (lap, i) {
    var cy = top + metaH + headH + rowH * i + rowH / 2;
    ctx.textBaseline = 'middle';
    ctx.fillStyle = prefs.text;
    setFont(ctx, 600, fontSize);
    ctx.textAlign = 'left';
    var partial = lap.distance_km && lap.distance_km < 0.95;
    ctx.fillText(partial ? formatNumber(lap.distance_km, 1) : String(i + 1), left, cy);
    ctx.textAlign = 'right';
    ctx.fillText(formatPace(lap.pace_sec_per_km), right, cy);

    if (!(speeds[i] > 0)) return;
    var frac = maxS > minS ? (speeds[i] - minS) / (maxS - minS) : 1;
    var len = barMax * (0.25 + 0.75 * frac);
    var b = bucketOf({ pace_sec_per_km: lap.pace_sec_per_km, hr_bpm: lap.avg_hr_bpm });
    ctx.strokeStyle = !gradient ? prefs.line : (b === null ? colors[2] : colors[b]);
    ctx.lineWidth = thick;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(barLeft + thick / 2, cy);
    ctx.lineTo(barLeft + Math.max(thick / 2, len - thick / 2), cy);
    ctx.stroke();
  });

  if (stats.length) {
    drawStatGrid(ctx, stats, grid, left, right - left, top + metaH + headH + rowH * laps.length + 40, prefs.text);
  }
}

function draw(canvas, detail, prefs) {
  var ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  if (prefs.template === 'splits') drawSplitsTemplate(ctx, detail, prefs);
  else drawRouteTemplate(ctx, detail, prefs);
}

// ------------------------------------------------------------- 控制項

function segmented(options, current, onPick, disabled) {
  var group = h('div', 'route-mode');
  group.setAttribute('role', 'group');
  options.forEach(function (o) {
    var btn = h('button', 'route-mode-btn' + (o.key === current ? ' is-active' : ''), o.label);
    btn.type = 'button';
    btn.setAttribute('aria-pressed', o.key === current ? 'true' : 'false');
    if (disabled && disabled(o.key)) btn.disabled = true;
    btn.addEventListener('click', function () { onPick(o.key); });
    group.appendChild(btn);
  });
  return group;
}

function swatches(current, onPick) {
  var wrap = h('div', 'share-swatches');
  wrap.setAttribute('role', 'group');
  wrap.setAttribute('aria-label', '線條顏色');
  LINE_COLORS.forEach(function (c) {
    var gradient = c.key === 'pace' || c.key === 'hr';
    var btn = h('button', 'share-swatch' + (gradient ? ' share-swatch-ramp' : '') + (c.key === current ? ' is-active' : ''));
    btn.type = 'button';
    btn.title = c.label;
    btn.setAttribute('aria-label', c.label);
    btn.setAttribute('aria-pressed', c.key === current ? 'true' : 'false');
    var dot = h('span', 'share-swatch-dot');
    if (gradient) {
      dot.style.background = 'linear-gradient(90deg,' + BUCKET_VARS.map(function (v) { return 'var(' + v + ')'; }).join(',') + ')';
      btn.appendChild(dot);
      btn.appendChild(h('span', null, c.label));
    } else {
      dot.style.background = c.key;
      btn.appendChild(dot);
    }
    btn.addEventListener('click', function () { onPick(c.key); });
    wrap.appendChild(btn);
  });
  return wrap;
}

function fieldList(detail, prefs, onChange) {
  var list = h('ul', 'share-fields');
  var s = detail.summary || {};
  prefs.order.forEach(function (key, i) {
    var f = fieldDef(key);
    var available = f.value(detail, s) !== null;
    var li = h('li', 'share-field' + (available ? '' : ' is-unavailable'));
    var label = h('label', 'share-field-label');
    var box = h('input');
    box.type = 'checkbox';
    box.checked = prefs.checked.indexOf(key) !== -1;
    box.disabled = !available;
    box.addEventListener('change', function () {
      prefs.checked = prefs.checked.filter(function (k) { return k !== key; });
      if (box.checked) prefs.checked.push(key);
      onChange(false);
    });
    label.appendChild(box);
    label.appendChild(h('span', null, f.label + (available ? '' : '（無資料）')));
    li.appendChild(label);
    [[-1, '↑', '上移'], [1, '↓', '下移']].forEach(function (mv) {
      var btn = h('button', 'share-field-move', mv[1]);
      btn.type = 'button';
      btn.setAttribute('aria-label', f.label + mv[2]);
      var j = i + mv[0];
      btn.disabled = j < 0 || j >= prefs.order.length;
      btn.addEventListener('click', function () {
        prefs.order[i] = prefs.order[j];
        prefs.order[j] = key;
        onChange(true);
      });
      li.appendChild(btn);
    });
    list.appendChild(li);
  });
  return list;
}

function control(label, node) {
  var row = h('div', 'share-control');
  row.appendChild(h('div', 'share-control-label', label));
  row.appendChild(node);
  return row;
}

function fileName(detail, template) {
  var day = (detail.started_at || '').slice(0, 10).replace(/-/g, '');
  return 'run_' + (day || detail.id) + '_' + template + '.png';
}

/** 在 node 內畫出分享圖的設定區＋預覽＋下載鈕 */
export function renderShareImage(node, detail) {
  var prefs = loadPrefs();
  var routeOk = hasRoute(detail);
  var lapsOk = hasLaps(detail);
  if (prefs.template === 'route' && !routeOk) prefs.template = 'splits';
  if (prefs.template === 'splits' && !lapsOk) prefs.template = 'route';

  var wrap = h('div', 'share');
  var controls = h('div', 'share-controls');
  var stage = h('div', 'share-stage');
  var canvas = h('canvas', 'share-canvas');
  canvas.width = W;
  canvas.height = H;
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', '分享圖預覽');
  stage.appendChild(canvas);

  var actions = h('div', 'share-actions');
  var download = h('button', 'share-btn share-btn-primary', '下載 PNG');
  download.type = 'button';
  actions.appendChild(download);
  var shareBtn = null;
  if (navigator.canShare && navigator.canShare({ files: [new File([''], 'x.png', { type: 'image/png' })] })) {
    // 手機：系統分享選單可直接存到相簿或丟到 IG 限動
    shareBtn = h('button', 'share-btn', '分享…');
    shareBtn.type = 'button';
    actions.appendChild(shareBtn);
  }
  actions.appendChild(h('span', 'chart-note share-note', '透明背景 1080×1920，預覽底下的格紋代表透明。'));

  wrap.appendChild(controls);
  var right = h('div', 'share-preview');
  right.appendChild(stage);
  right.appendChild(actions);
  wrap.appendChild(right);
  node.replaceChildren(wrap);

  function update(rebuild) {
    savePrefs(prefs);
    if (rebuild) buildControls();
    stage.classList.toggle('share-stage-light', prefs.text !== '#FFFFFF');
    draw(canvas, detail, prefs);
  }

  function buildControls() {
    controls.replaceChildren(
      control('版型', segmented(TEMPLATES, prefs.template, function (k) {
        prefs.template = k;
        update(true);
      }, function (k) { return k === 'route' ? !routeOk : !lapsOk; })),
      control(prefs.template === 'route' ? '路線顏色' : '長條顏色', swatches(prefs.line, function (k) {
        prefs.line = k;
        update(true);
      })),
      control('文字顏色', segmented(TEXT_COLORS, prefs.text, function (k) {
        prefs.text = k;
        update(true);
      })),
      control('數據欄位', fieldList(detail, prefs, update))
    );
  }

  function withBlob(fn) {
    canvas.toBlob(function (blob) { if (blob) fn(blob); }, 'image/png');
  }

  download.addEventListener('click', function () {
    withBlob(function (blob) {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = fileName(detail, prefs.template);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
  });
  if (shareBtn) {
    shareBtn.addEventListener('click', function () {
      withBlob(function (blob) {
        var file = new File([blob], fileName(detail, prefs.template), { type: 'image/png' });
        navigator.share({ files: [file] }).catch(function () { /* 使用者取消分享 */ });
      });
    });
  }

  buildControls();
  update(false);
}
