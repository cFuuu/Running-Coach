/**
 * tools.js — 配速計算機（#/tools）：配速／田徑場／完成時間／跑步機四區同頁連動
 *
 * 手機優先：每區一張卡，標題列有類別色與單色 icon；一次只顯示一個單位的值，
 * 單位用標題列右側的切換鍵換（配速 /km↔/mile、時速 km/h↔mph），完成時間用
 * 下拉選距離。
 *
 * 四區共用一個速度（tools.secPerKm）：改任何一格都先換算成每公里秒數，再回填
 * 其他所有欄位——正在打字的那格不回填，免得游標被打斷，離開欄位時才整理格式。
 * 時間類欄位是分段輸入（分／秒、時／分／秒各一格，見 core/clock-field.js）。
 *
 * 全是純算術、不打 API、不讀訓練資料，所以這支檔案同時是 dashboard 的一頁，
 * 也是離線單檔 pace-calculator.html 的主體（scripts/build_pace_calculator.py 打包）。
 * 因此這裡**不可** import state／api／facts 這類 dashboard 專屬模組。
 */

import { $, h } from '../core/dom.js';
import { clockField } from '../core/clock-field.js';
import { KM_PER_MILE, laneLengthM, formatSplit, paceToKmh, kmhToPace } from '../core/pace.js';

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

// 田徑場分段表的列：一圈的幾分之幾（1 圈以下是 100／200／300m 分段）
var LAP_FRACTIONS = [0.25, 0.5, 0.75, 1, 2, 3, 4, 5];

// 標題列的單色 icon（24x24、stroke 用 currentColor，顏色跟著標題列文字色）
var ICONS = {
  pace: [
    ['path', { d: 'M4.5 17a8.5 8.5 0 1 1 15 0' }],
    ['path', { d: 'M12 16l3.5-5' }],
    ['path', { d: 'M9 21h6' }]
  ],
  track: [
    ['rect', { x: 3, y: 6, width: 18, height: 12, rx: 6 }],
    ['rect', { x: 7, y: 10, width: 10, height: 4, rx: 2 }]
  ],
  finish: [
    ['circle', { cx: 12, cy: 14, r: 7 }],
    ['path', { d: 'M12 14v-3.5M9.5 3h5M12 3v4M18.2 7.3l1.3-1.3' }]
  ],
  treadmill: [
    ['circle', { cx: 13, cy: 4.5, r: 1.6 }],
    ['path', { d: 'M11 8.5l2.5 2.5-2 3.5M13.5 11l3 2.5M11.5 14.5L9 17.5' }],
    ['path', { d: 'M3 20.5h14M19.5 20.5v-9H17' }]
  ]
};
var SVG_NS = 'http://www.w3.org/2000/svg';
var ICON_SIZE_PX = 34;

var tools = {
  built: false,
  secPerKm: null,
  trackM: TRACK_OPTIONS[0].meters,
  paceUnit: 'km',                          // 'km'｜'mile'
  speedUnit: 'kmh',                        // 'kmh'｜'mph'
  finishKm: RACE_DISTANCES[RACE_DISTANCES.length - 1].km, // 預設全馬（同原網站）
  fields: [],   // 可輸入的欄位：{ owner, write(secPerKm|null) }；owner 是事件來源，回填時跳過
  outputs: []   // 唯讀的換算結果：{ node, toText(secPerKm) }
};

// ------------------------------------------------------------ 換算主流程

function setSpeed(secPerKm, editing) {
  tools.secPerKm = secPerKm;
  syncFields(editing);
}

/** 依目前速度回填所有欄位；editing 是使用者正在打字的那格，跳過不動。 */
function syncFields(editing) {
  var s = tools.secPerKm;
  tools.fields.forEach(function (f) {
    if (f.owner !== editing) f.write(s);
  });
  tools.outputs.forEach(function (o) {
    o.node.textContent = s === null ? '—' : o.toText(s);
  });
}

/**
 * 把一組分段輸入接進連動：打字即換算，離開時把自己整理成標準格式。
 * 全空＝清空所有欄位；格式錯誤（秒 ≥ 60 等）只標紅框，不動其他欄位。
 * toTotal／fromTotal 在每次換算時才呼叫，所以可以讀「目前單位」這類會變的狀態。
 * @param {Object} clock clockField() 的回傳值
 * @param {function} toTotal 每公里秒數 → 這一欄的總秒數
 * @param {function} fromTotal 這一欄的總秒數 → 每公里秒數
 */
function bindClock(clock, toTotal, fromTotal) {
  var field = {
    owner: clock.node,
    write: function (s) {
      clock.setInvalid(false);
      clock.write(s === null ? null : toTotal(s));
    }
  };
  tools.fields.push(field);
  clock.onChange(function () {
    var total = clock.read();
    if (total === null) {
      clock.setInvalid(false);
      setSpeed(null, clock.node);
    } else if (isNaN(total)) {
      clock.setInvalid(true);
    } else {
      clock.setInvalid(false);
      setSpeed(fromTotal(total), clock.node);
    }
  });
  clock.onBlur(function () {
    if (tools.secPerKm !== null && !clock.isInvalid()) field.write(tools.secPerKm);
  });
  return clock.node;
}

/** 單一數字欄（跑步機時速）。行為同 bindClock，只是整欄一個 input。 */
function bindNumber(input, toText, fromNumber) {
  var field = {
    owner: input,
    write: function (s) {
      input.value = s === null ? '' : toText(s);
      input.classList.remove('is-invalid');
    }
  };
  tools.fields.push(field);
  input.addEventListener('input', function () {
    var raw = input.value.trim();
    if (raw === '') {
      input.classList.remove('is-invalid');
      setSpeed(null, input);
      return;
    }
    var n = /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
    var ok = n > 0 && isFinite(n);
    input.classList.toggle('is-invalid', !ok);
    if (ok) setSpeed(fromNumber(n), input);
  });
  input.addEventListener('change', function () {
    if (tools.secPerKm !== null && !input.classList.contains('is-invalid')) field.write(tools.secPerKm);
  });
  return input;
}

// ------------------------------------------------------------ 共用元件

function icon(kind) {
  var svg = document.createElementNS(SVG_NS, 'svg');
  var attrs = {
    viewBox: '0 0 24 24', width: ICON_SIZE_PX, height: ICON_SIZE_PX, fill: 'none', stroke: 'currentColor',
    'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'
  };
  Object.keys(attrs).forEach(function (k) { svg.setAttribute(k, attrs[k]); });
  ICONS[kind].forEach(function (shape) {
    var node = document.createElementNS(SVG_NS, shape[0]);
    Object.keys(shape[1]).forEach(function (k) { node.setAttribute(k, shape[1][k]); });
    svg.appendChild(node);
  });
  return svg;
}

/**
 * 一張區塊卡：類別色標題列（icon＋標題，右側可放切換鍵）＋內容。
 * @param {string} kind ICONS 的 key，同時決定標題列的類別色（CSS .tools-theme-*）
 * @param {string} title
 * @param {?HTMLElement} headControl 放在標題列右側的元素（單位切換鍵）
 * @returns {{sec:HTMLElement, body:HTMLElement}}
 */
function panel(kind, title, headControl) {
  var sec = h('section', 'panel tools-panel tools-theme-' + kind);
  var head = h('div', 'tools-panel-head');
  head.appendChild(icon(kind));
  head.appendChild(h('h2', 'tools-panel-title', title));
  if (headControl) head.appendChild(headControl);
  sec.appendChild(head);
  var body = h('div', 'tools-panel-body');
  sec.appendChild(body);
  return { sec: sec, body: body };
}

/** 二選一的單位切換鍵（同一時間只有一個單位的值顯示在畫面上）。 */
function unitSwitch(ariaLabel, options, initialKey, onChange) {
  var group = h('div', 'tools-switch');
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', ariaLabel);
  var buttons = options.map(function (opt) {
    var btn = h('button', null, opt.label);
    btn.type = 'button';
    btn.addEventListener('click', function () {
      if (btn.getAttribute('aria-pressed') === 'true') return;
      mark(opt.key);
      onChange(opt.key);
    });
    group.appendChild(btn);
    return { key: opt.key, node: btn };
  });
  function mark(activeKey) {
    buttons.forEach(function (b) {
      var on = b.key === activeKey;
      b.node.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.node.classList.toggle('is-active', on);
    });
  }
  mark(initialKey);
  return group;
}

function selectField(ariaLabel, optionLabels, selectedIndex, onChange) {
  var select = h('select', 'tools-select');
  select.setAttribute('aria-label', ariaLabel);
  optionLabels.forEach(function (label, i) {
    var o = h('option', null, label);
    o.value = String(i);
    select.appendChild(o);
  });
  select.value = String(selectedIndex);
  select.addEventListener('change', function () { onChange(Number(select.value)); });
  return select;
}

function inputRow(control, unitText) {
  var row = h('div', 'tools-row');
  row.appendChild(control);
  var unit = h('span', 'tools-unit', unitText);
  row.appendChild(unit);
  return { row: row, unit: unit };
}

// ------------------------------------------------------------ 四個區塊

function buildPacePanel() {
  var factor = function () { return tools.paceUnit === 'mile' ? KM_PER_MILE : 1; };
  var clock = bindClock(
    clockField({ parts: ['m', 's'], ariaLabel: '配速', placeholders: ['5', '30'], plainUnitSec: 60, size: 'lg' }),
    function (s) { return s * factor(); },
    function (t) { return t / factor(); }
  );
  var line = inputRow(clock, '/km');
  var toggle = unitSwitch('配速單位', [{ key: 'km', label: '/km' }, { key: 'mile', label: '/mile' }],
    tools.paceUnit, function (key) {
      tools.paceUnit = key;
      line.unit.textContent = key === 'mile' ? '/mile' : '/km';
      syncFields(null);
    });
  var p = panel('pace', '配速', toggle);
  p.body.appendChild(line.row);
  return p.sec;
}

function buildTrackPanel() {
  var p = panel('track', '田徑場');

  p.body.appendChild(selectField('道次',
    TRACK_OPTIONS.map(function (opt) { return opt.label + '（' + Number(opt.meters.toFixed(1)) + 'm）'; }),
    0,
    function (i) {
      tools.trackM = TRACK_OPTIONS[i].meters;
      renderLapLabels();
      syncFields(null);
    }));

  var clock = bindClock(
    clockField({
      parts: ['m', 's'], ariaLabel: '每圈時間', decimalSeconds: true, blankLeadingZero: true,
      placeholders: ['0', '96'], plainUnitSec: 1, size: 'lg'
    }),
    function (s) { return s * tools.trackM / 1000; },
    function (t) { return t / (tools.trackM / 1000); }
  );
  p.body.appendChild(inputRow(clock, '/圈').row);

  // 分段時間預設收合（手機上省空間），按「＋」展開
  var toggle = h('button', 'tools-expand', '分段時間');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  p.body.appendChild(toggle);

  var table = h('table', 'tools-table');
  table.hidden = true;
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
  p.body.appendChild(table);

  toggle.addEventListener('click', function () {
    var open = table.hidden;
    table.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  function renderLapLabels() {
    labels.forEach(function (l) {
      var meters = Math.round(tools.trackM * l.frac);
      l.node.textContent = l.frac < 1 ? meters + ' m' : l.frac + ' 圈 · ' + meters + ' m';
    });
  }
  renderLapLabels();
  return p.sec;
}

function buildFinishPanel() {
  var p = panel('finish', '完成時間');
  var defaultIndex = RACE_DISTANCES.map(function (d) { return d.km; }).indexOf(tools.finishKm);
  p.body.appendChild(selectField('距離', RACE_DISTANCES.map(function (d) { return d.label; }), defaultIndex,
    function (i) {
      tools.finishKm = RACE_DISTANCES[i].km;
      syncFields(null);
    }));
  p.body.appendChild(bindClock(
    clockField({
      parts: ['h', 'm', 's'], ariaLabel: '完成時間', blankLeadingZero: true,
      placeholders: ['0', '00', '00'], plainUnitSec: 60, size: 'lg'
    }),
    function (s) { return s * tools.finishKm; },
    function (t) { return t / tools.finishKm; }
  ));
  return p.sec;
}

function buildTreadmillPanel() {
  var factor = function () { return tools.speedUnit === 'mph' ? KM_PER_MILE : 1; };
  var input = h('input', 'tools-input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = '10.9';
  input.setAttribute('aria-label', '時速');
  bindNumber(
    input,
    function (s) { return (paceToKmh(s) / factor()).toFixed(2); },
    function (v) { return kmhToPace(v * factor()); }
  );
  var line = inputRow(input, 'km/h');
  var toggle = unitSwitch('時速單位', [{ key: 'kmh', label: 'km/h' }, { key: 'mph', label: 'mph' }],
    tools.speedUnit, function (key) {
      tools.speedUnit = key;
      line.unit.textContent = key === 'mph' ? 'mph' : 'km/h';
      syncFields(null);
    });
  var p = panel('treadmill', '跑步機', toggle);
  p.body.appendChild(line.row);
  return p.sec;
}

// ------------------------------------------------------------ 入口

/** 第一次切到工具頁時建立 DOM（之後只切 hidden，輸入值保留）。 */
export function renderToolsPage() {
  if (tools.built) return;
  var body = $('tools-body');
  if (!body) return;
  tools.built = true;
  body.appendChild(h('p', 'tools-intro',
    '任一格輸入，其他格即時換算。時間分格填寫，打完一格按 : 或 Tab 跳下一格，空格視為 0。'));
  var grid = h('div', 'tools-grid');
  grid.appendChild(buildPacePanel());
  grid.appendChild(buildTrackPanel());
  grid.appendChild(buildFinishPanel());
  grid.appendChild(buildTreadmillPanel());
  body.appendChild(grid);
}
