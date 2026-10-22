/**
 * tools.js — 配速計算機（#/tools）：配速／跑步機／田徑場／完成時間四區同頁連動
 *
 * 四區共用一個速度（tools.secPerKm）：改任何一格都先換算成每公里秒數，再回填
 * 其他所有欄位——正在打字的那格不回填，免得游標被打斷，離開欄位時才整理格式。
 * 等速換算全在前端（core/pace.js）；完成時間的「跑力推估」欄走
 * /api/tools/race-predict（Daniels 公式，與課表、戰力綜覽同一套）。
 *
 * 不吃頂列的時間範圍，也不讀任何訓練資料；只有「帶入目前跑力」會用到首頁
 * 已載入的 VDOT。
 */

import * as C from '../charts.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { $, clear, h } from '../core/dom.js';
import { confBadge, infoTip } from '../core/facts.js';
import { KM_PER_MILE, laneLengthM, parseClock, formatSplit, paceToKmh, kmhToPace } from '../core/pace.js';

var RACE_DISTANCES = [
  { km: 0.8, label: '800m' },
  { km: 1.5, label: '1500m' },
  { km: 3, label: '3000m' },
  { km: 5, label: '5000m' },
  { km: 10, label: '10K' },
  { km: 21.0975, label: '半馬' },
  { km: 42.195, label: '全馬' }
];

var WARMUP_TRACK_M = 300;
var TRACK_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8].map(function (lane) {
  return { meters: laneLengthM(lane), label: '第 ' + lane + ' 道' };
}).concat([{ meters: WARMUP_TRACK_M, label: '暖身場' }]);

// 田徑場表格的列：一圈的幾分之幾（1 圈以下是 100／200／300m 分段）
var LAP_FRACTIONS = [0.25, 0.5, 0.75, 1, 2, 3, 4, 5];

var ZONE_LABEL = { easy: '輕鬆 E', marathon: '馬拉松 M', tempo: '閾值 T', interval: '間歇 I' };

var PREDICT_DEBOUNCE_MS = 300;

var tools = {
  built: false,
  secPerKm: null,
  trackM: TRACK_OPTIONS[0].meters,
  // 跑力推估以哪一列的等速成績為基準；預設全馬（同原網站的預設距離）
  refIndex: RACE_DISTANCES.length - 1,
  fields: [],   // 可輸入的欄位：{ input, toText(secPerKm), fromText(text) → secPerKm|null, onEdit? }
  outputs: [],  // 唯讀的換算結果：{ node, toText(secPerKm) }
  rows: [],     // 完成時間每列的節點：{ tr, refBtn, pred }
  predictTimer: null,
  predictSeq: 0
};

// ------------------------------------------------------------ 換算主流程

function setSpeed(secPerKm, editing) {
  tools.secPerKm = secPerKm;
  syncFields(editing);
  schedulePredict();
}

/** 依目前速度回填所有欄位；editing 是使用者正在打字的那格，跳過不動。 */
function syncFields(editing) {
  var s = tools.secPerKm;
  tools.fields.forEach(function (f) {
    if (f.input === editing) return;
    f.input.value = s === null ? '' : f.toText(s);
    f.input.classList.remove('is-invalid');
  });
  tools.outputs.forEach(function (o) {
    o.node.textContent = s === null ? '—' : o.toText(s);
  });
}

/**
 * 把一個 input 接進連動：打字即換算，離開欄位時把自己整理成標準格式。
 * 空白＝清空所有欄位；格式錯誤只標紅框，不動其他欄位。
 */
function bindField(input, toText, fromText, onEdit) {
  var field = { input: input, toText: toText, fromText: fromText };
  tools.fields.push(field);
  input.addEventListener('input', function () {
    if (input.value.trim() === '') {
      input.classList.remove('is-invalid');
      setSpeed(null, input);
      return;
    }
    var s = fromText(input.value);
    var ok = s !== null && isFinite(s) && s > 0;
    input.classList.toggle('is-invalid', !ok);
    if (!ok) return;
    if (onEdit) onEdit();
    setSpeed(s, input);
  });
  input.addEventListener('change', function () {
    if (tools.secPerKm !== null && !input.classList.contains('is-invalid')) {
      input.value = toText(tools.secPerKm);
    }
  });
  return input;
}

function textInput(placeholder, ariaLabel) {
  var input = h('input', 'tools-input');
  input.type = 'text';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = placeholder;
  input.setAttribute('aria-label', ariaLabel);
  return input;
}

function parseSpeed(text) {
  var raw = String(text || '').trim();
  return /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : null;
}

function labeledField(label, unit, input) {
  var wrap = h('label', 'tools-field');
  wrap.appendChild(h('span', 'tools-field-label', label));
  var row = h('span', 'tools-field-row');
  row.appendChild(input);
  if (unit) row.appendChild(h('span', 'tools-unit', unit));
  wrap.appendChild(row);
  return wrap;
}

function panel(title, hint) {
  var sec = h('section', 'panel tools-panel');
  var h2 = h('h2', 'panel-title', title);
  if (hint) h2.appendChild(h('span', 'panel-hint', hint));
  sec.appendChild(h2);
  return sec;
}

// ------------------------------------------------------------ 四個區塊

function buildPacePanel() {
  var sec = panel('配速');
  var grid = h('div', 'tools-fields');
  grid.appendChild(labeledField('每公里', '/km', bindField(
    textInput('5:30', '每公里配速'),
    function (s) { return C.formatPace(s); },
    function (t) { return parseClock(t, 60); }
  )));
  grid.appendChild(labeledField('每英里', '/mi', bindField(
    textInput('8:51', '每英里配速'),
    function (s) { return C.formatPace(s * KM_PER_MILE); },
    function (t) { var v = parseClock(t, 60); return v === null ? null : v / KM_PER_MILE; }
  )));
  sec.appendChild(grid);
  return sec;
}

function buildTreadmillPanel() {
  var sec = panel('跑步機');
  var grid = h('div', 'tools-fields');
  grid.appendChild(labeledField('公里時速', 'km/h', bindField(
    textInput('10.9', '公里時速'),
    function (s) { return paceToKmh(s).toFixed(2); },
    function (t) { var v = parseSpeed(t); return v ? kmhToPace(v) : null; }
  )));
  grid.appendChild(labeledField('英里時速', 'mph', bindField(
    textInput('6.78', '英里時速'),
    function (s) { return (paceToKmh(s) / KM_PER_MILE).toFixed(2); },
    function (t) { var v = parseSpeed(t); return v ? kmhToPace(v * KM_PER_MILE) : null; }
  )));
  sec.appendChild(grid);
  return sec;
}

function buildTrackPanel() {
  var sec = panel('田徑場', '換道時速度不變，重算每圈秒數');
  var controls = h('div', 'tools-fields');

  var select = h('select', 'tools-input tools-select');
  select.setAttribute('aria-label', '道次');
  TRACK_OPTIONS.forEach(function (opt, i) {
    var o = h('option', null, opt.label + '（' + opt.meters.toFixed(opt.meters % 1 ? 1 : 0) + 'm）');
    o.value = String(i);
    select.appendChild(o);
  });
  var laneWrap = h('label', 'tools-field');
  laneWrap.appendChild(h('span', 'tools-field-label', '道次'));
  laneWrap.appendChild(select);
  controls.appendChild(laneWrap);

  controls.appendChild(labeledField('每圈', '秒', bindField(
    textInput('96', '每圈秒數'),
    function (s) { return formatSplit(s * tools.trackM / 1000); },
    function (t) { var v = parseClock(t, 1); return v === null ? null : v / (tools.trackM / 1000); }
  )));
  sec.appendChild(controls);

  var table = h('table', 'tools-table');
  var tbody = h('tbody');
  var labels = [];
  LAP_FRACTIONS.forEach(function (frac) {
    var tr = h('tr');
    var th = h('th');
    th.scope = 'row';
    labels.push({ node: th, frac: frac });
    tr.appendChild(th);
    var td = h('td', 'tools-num', '—');
    tools.outputs.push({ node: td, toText: function (s) { return formatSplit(s * tools.trackM * frac / 1000); } });
    tr.appendChild(td);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  sec.appendChild(table);

  function renderLapLabels() {
    labels.forEach(function (l) {
      var meters = Math.round(tools.trackM * l.frac);
      l.node.textContent = l.frac < 1 ? meters + ' m' : l.frac + ' 圈 · ' + meters + ' m';
    });
  }
  select.addEventListener('change', function () {
    tools.trackM = TRACK_OPTIONS[Number(select.value)].meters;
    renderLapLabels();
    syncFields(null);
  });
  renderLapLabels();
  return sec;
}

function buildFinishPanel() {
  var sec = panel('完成時間', '左欄等速換算；右欄依標示列的成績用 Daniels 公式推估');

  var head = h('div', 'tools-predict-head');
  var basis = h('p', 'tools-predict-basis', '輸入任一欄後顯示跑力推估');
  basis.id = 'tools-predict-basis';
  head.appendChild(basis);
  var useVdot = h('button', 'range-btn', '帶入目前跑力');
  useVdot.type = 'button';
  useVdot.addEventListener('click', applyCurrentVdot);
  head.appendChild(useVdot);
  head.appendChild(infoTip('跑力推估', [
    '把標示列（◉）的等速完成時間當作一場全力成績，代入 Daniels & Gilbert 公式反推 VDOT，'
      + '再反解其他距離的等效成績。點距離名稱可換基準列。',
    '與課表、首頁戰力綜覽用同一套公式（後端 vdot_engine）。',
    '由短距離推估半馬／全馬時，前提是已有足夠的長距離訓練量；週跑量少的話實際完賽會明顯慢於推估（標「低信心度」）。'
      + '1500m 以下屬公式外推。',
    '「帶入目前跑力」用首頁戰力綜覽的 VDOT（只採比賽成績）。'
  ]));
  sec.appendChild(head);

  var table = h('table', 'tools-table tools-finish-table');
  var thead = h('thead');
  var hr = h('tr');
  ['距離', '等速完成', '跑力推估'].forEach(function (t) {
    var th = h('th', null, t);
    th.scope = 'col';
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  var tbody = h('tbody');
  RACE_DISTANCES.forEach(function (d, i) {
    var tr = h('tr');
    var th = h('th');
    th.scope = 'row';
    var refBtn = h('button', 'tools-ref-btn', d.label);
    refBtn.type = 'button';
    refBtn.addEventListener('click', function () { setRef(i); schedulePredict(true); });
    th.appendChild(refBtn);
    tr.appendChild(th);

    var td = h('td');
    td.appendChild(bindField(
      textInput('h:m:s', d.label + '完成時間'),
      function (s) { return C.formatDuration(s * d.km); },
      function (t) { var v = parseClock(t, 60); return v === null ? null : v / d.km; },
      function () { setRef(i); }
    ));
    tr.appendChild(td);

    var pred = h('td', 'tools-num tools-pred', '—');
    tr.appendChild(pred);
    tbody.appendChild(tr);
    tools.rows.push({ tr: tr, refBtn: refBtn, pred: pred });
  });
  table.appendChild(tbody);
  sec.appendChild(table);

  var zones = h('div', 'tools-zones');
  zones.id = 'tools-zones';
  sec.appendChild(zones);
  setRef(tools.refIndex);
  return sec;
}

// ------------------------------------------------------------ 跑力推估

function setRef(index) {
  tools.refIndex = index;
  tools.rows.forEach(function (r, i) {
    r.tr.classList.toggle('is-ref', i === index);
    r.refBtn.setAttribute('aria-pressed', i === index ? 'true' : 'false');
  });
}

function schedulePredict(immediate) {
  clearTimeout(tools.predictTimer);
  if (tools.secPerKm === null) {
    tools.predictSeq += 1; // 作廢還在路上的請求
    renderPrediction(null);
    return;
  }
  var ref = RACE_DISTANCES[tools.refIndex];
  var params = 'distance_km=' + ref.km + '&time_sec=' + Math.round(tools.secPerKm * ref.km);
  tools.predictTimer = setTimeout(function () {
    requestPredict(params, ref.label + ' ' + C.formatDuration(tools.secPerKm * ref.km));
  }, immediate ? 0 : PREDICT_DEBOUNCE_MS);
}

/** @returns {Promise<?Object>} 回傳 API 結果；被較新的請求取代或失敗時為 null */
function requestPredict(params, basisText) {
  var seq = ++tools.predictSeq;
  var targets = RACE_DISTANCES.map(function (d) { return d.km; }).join(',');
  return api('/api/tools/race-predict?targets=' + targets + '&' + params)
    .then(function (res) {
      if (seq !== tools.predictSeq) return null;
      renderPrediction(res, basisText);
      return res;
    })
    .catch(function (err) {
      if (seq !== tools.predictSeq) return null;
      renderPrediction(null, null, err.message);
      return null;
    });
}

function renderPrediction(res, basisText, error) {
  var basis = $('tools-predict-basis');
  var zones = $('tools-zones');
  clear(basis);
  clear(zones);
  tools.rows.forEach(function (r) { clear(r.pred); r.pred.textContent = '—'; });
  if (!res) {
    basis.textContent = error ? '無法推估：' + error : '輸入任一欄後顯示跑力推估';
    basis.classList.toggle('is-error', !!error);
    return;
  }
  basis.classList.remove('is-error');
  basis.appendChild(document.createTextNode('基準 ' + basisText + ' → VDOT '
    + C.formatNumber(res.vdot.value, 1)));

  res.predictions.forEach(function (p, i) {
    var cell = tools.rows[i].pred;
    clear(cell);
    var fact = p.time_sec;
    if (!fact) { cell.textContent = '未評'; return; }
    cell.appendChild(h('span', null, C.formatDuration(fact.value)));
    cell.appendChild(h('span', 'tools-pred-pace', C.formatPace(fact.value / p.distance_km) + '/km'));
    if (fact.confidence.level !== 'high') cell.appendChild(confBadge(fact));
  });

  zones.appendChild(h('span', 'tools-zones-title', '訓練配速'));
  Object.keys(ZONE_LABEL).forEach(function (key) {
    var z = res.pace_zones[key];
    if (!z) return;
    var chip = h('span', 'tools-zone');
    chip.appendChild(h('span', 'tools-zone-name', ZONE_LABEL[key]));
    chip.appendChild(h('span', 'tools-num',
      C.formatPace(z.fast_sec_per_km) + '–' + C.formatPace(z.slow_sec_per_km)));
    zones.appendChild(chip);
  });
}

/** 用首頁戰力綜覽的 VDOT 推估，並把速度設成「基準列的推估成績」。 */
function applyCurrentVdot() {
  var ov = state.homeOverview;
  var fact = ov && ov.vdot && ov.vdot.race && ov.vdot.race.value;
  if (!fact) {
    renderPrediction(null, null, ov
      ? ((ov.vdot && ov.vdot.race && ov.vdot.race.reason) || '尚無跑力資料')
      : '首頁資料還在載入，請稍後再試');
    return;
  }
  clearTimeout(tools.predictTimer);
  requestPredict('vdot=' + fact.value, '目前跑力').then(function (res) {
    if (!res) return;
    var ref = RACE_DISTANCES[tools.refIndex];
    var refFact = res.predictions[tools.refIndex].time_sec;
    if (!refFact) return;
    tools.secPerKm = refFact.value / ref.km;
    syncFields(null); // 只回填欄位，不重新推估——推估結果就是這次的回應
  });
}

// ------------------------------------------------------------ 入口

/** 第一次切到工具頁時建立 DOM（之後只切 hidden，輸入值保留）。 */
export function renderToolsPage() {
  if (tools.built) return;
  var body = $('tools-body');
  if (!body) return;
  tools.built = true;
  body.appendChild(h('p', 'tools-intro',
    '任一欄輸入，其他欄即時換算（等速）。時間可輸入 m:s 或 h:m:s；只填數字時配速與完成時間視為分鐘、每圈視為秒。'));
  var grid = h('div', 'tools-grid');
  grid.appendChild(buildPacePanel());
  grid.appendChild(buildTreadmillPanel());
  grid.appendChild(buildTrackPanel());
  grid.appendChild(buildFinishPanel());
  body.appendChild(grid);
}
