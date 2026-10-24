/**
 * tools.js — 配速計算機（#/tools）：配速／田徑場／完成時間／跑步機四區同頁連動
 *
 * 手機優先：每區一張卡，標題列有類別色與單色 icon；一次只顯示一個單位的值，
 * 單位用標題列右側的切換鍵換（配速 /km↔/mile、時速 km/h↔mph），完成時間用
 * 下拉選距離。
 *
 * 四區共用一個速度（tools.secPerKm）：改任何一格都先換算成每公里秒數，再回填
 * 其他所有欄位——正在打字的那格不回填，免得游標被打斷，離開欄位時才整理格式。
 * 時間類欄位是分段輸入（分／秒、時／分／秒各一格，見 core/clock-field.js）；
 * 田徑場的每圈改成單一「秒數」輸入（田徑場習慣直接講秒），算出每 100m 各段秒數與配速。
 *
 * 全是純算術、不打 API、不讀訓練資料，所以這支檔案同時是 dashboard 的一頁，
 * 也是離線單檔 pace-calculator.html 的主體（scripts/build_pace_calculator.py 打包）。
 * 因此這裡**不可** import state／api／facts 這類 dashboard 專屬模組。
 */

import { $, h } from '../core/dom.js';
import { clockField } from '../core/clock-field.js';
// 打包腳本只支援單行 import，勿拆行
import { KM_PER_MILE, laneLengthM, parseClock, formatSplit, formatMinSec, paceToKmh, kmhToPace } from '../core/pace.js';

var RACE_DISTANCES = [
  { km: 0.8, label: '800m' },
  { km: 1.5, label: '1500m' },
  { km: 3, label: '3000m' },
  { km: 5, label: '5000m' },
  { km: 10, label: '10 km' },
  { km: 21.0975, label: '半馬', note: '21.0975 km' },
  { km: 42.195, label: '全馬', note: '42.195 km' }
];

var WARMUP_TRACK_M = 300;
var TRACK_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8].map(function (lane) {
  return { meters: laneLengthM(lane), label: '第 ' + lane + ' 道' };
}).concat([{ meters: WARMUP_TRACK_M, label: '暖身場' }]);

// 田徑場：一圈 400m 內每 100m 的分段，以及「多圈時間」表列出的圈數
var SPLIT_M = 100;
var SPLITS_PER_LAP = 4;
var MULTI_LAPS = [1, 2, 3, 4, 5];

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
var ICON_SIZE_PX = 28;

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

function parseDecimal(raw) {
  return /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
}

/** 每圈秒數：填「96」「96.5」或「1:36.5」都可以。看不懂回 NaN。 */
function parseLapText(raw) {
  var sec = parseClock(raw, 1);
  return sec === null ? NaN : sec;
}

/**
 * 單一輸入欄（跑步機時速、每圈秒數）。行為同 bindClock，只是整欄一個 input。
 * @param {function} [parse] 文字 → 數值，看不懂回 NaN；預設只收一般小數
 */
function bindNumber(input, toText, fromNumber, parse) {
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
    var n = (parse || parseDecimal)(raw);
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
 * 一張區塊卡（與首頁指標卡同一套）：類別色標題列（icon＋標題，右側可放切換鍵）＋內容。
 * @param {string} kind ICONS 的 key，同時決定標題列的類別色（CSS .tools-theme-*）
 * @param {string} title
 * @param {?HTMLElement} headControl 放在標題列右側的元素（單位切換鍵）
 * @returns {{sec:HTMLElement, body:HTMLElement}}
 */
function panel(kind, title, headControl) {
  var sec = h('section', 'tools-card tools-theme-' + kind);
  var head = h('div', 'tools-card-head');
  head.appendChild(icon(kind));
  head.appendChild(h('h3', 'tools-card-title', title));
  if (headControl) head.appendChild(headControl);
  sec.appendChild(head);
  var body = h('div', 'tools-card-body');
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

/** 一圈的秒數 → 輸入欄的顯示：純秒數、最多一位小數（96、96.5）。 */
function formatLapSeconds(sec) {
  return String(Number(sec.toFixed(1)));
}

/** 田徑場的一個結果欄：只在換算時由 syncFields 更新文字。 */
function outputCell(tag, className, toText) {
  var node = h(tag, className, '—');
  tools.outputs.push({ node: node, toText: toText });
  return node;
}

function buildTrackPanel() {
  var p = panel('track', '田徑場');
  // 每圈長度隨道次變（外道較長），所以「每 100m」與「每圈」都從同一個速度算，不直接互除
  var lapKm = function () { return tools.trackM / 1000; };

  p.body.appendChild(selectField('道次',
    TRACK_OPTIONS.map(function (opt) { return opt.label + '（' + Number(opt.meters.toFixed(1)) + 'm）'; }),
    0,
    function (i) {
      tools.trackM = TRACK_OPTIONS[i].meters;
      renderLapLabels();
      syncFields(null);
    }));

  // 田徑場習慣直接講秒數，所以這格是單一輸入（96、96.5，也收 1:36.5）
  var input = h('input', 'tools-input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = '96';
  input.setAttribute('aria-label', '每圈秒數');
  bindNumber(
    input,
    function (s) { return formatLapSeconds(s * lapKm()); },
    function (lapSec) { return lapSec / lapKm(); },
    parseLapText
  );
  // 同一圈速的「分:秒」寫法（96 ⇄ 1:36），兩格互相連動（2026-10-24 Fu）；放不下時換到下一行
  var lapClock = bindClock(
    clockField({ parts: ['m', 's'], ariaLabel: '每圈分秒', placeholders: ['1', '36'], decimalSeconds: true, plainUnitSec: 1, size: 'lg' }),
    function (s) { return s * lapKm(); },
    function (t) { return t / lapKm(); }
  );
  var lapRows = h('div', 'tools-row is-wrap');
  lapRows.appendChild(inputRow(input, '秒/圈').row);
  lapRows.appendChild(inputRow(lapClock, '分:秒/圈').row);
  p.body.appendChild(lapRows);

  // 換算結果：每 100m 秒數與配速（同一個速度的不同說法）
  var stats = h('div', 'tools-stats');
  [
    ['每 100m（秒）', function (s) { return formatSplit(s * SPLIT_M / 1000); }],
    ['配速（/km）', function (s) { return formatMinSec(s); }],
    ['配速（/mile）', function (s) { return formatMinSec(s * KM_PER_MILE); }],
    ['時速（km/h）', function (s) { return paceToKmh(s).toFixed(2); }]
  ].forEach(function (spec) {
    var tile = h('div', 'tools-stat');
    tile.appendChild(h('span', 'tools-stat-label', spec[0]));
    tile.appendChild(outputCell('span', 'tools-stat-value', spec[1]));
    stats.appendChild(tile);
  });
  p.body.appendChild(stats);

  // 一圈內每 100m：該段秒數與累計（等速，所以每段一樣）
  var splitTable = h('table', 'tools-table');
  var head = h('tr');
  ['區間', '該段（秒）', '累計（秒）'].forEach(function (text, i) {
    var th = h('th', i > 0 ? 'tools-num' : null, text);
    th.scope = 'col';
    head.appendChild(th);
  });
  splitTable.appendChild(h('thead')).appendChild(head);
  var splitBody = h('tbody');
  for (var k = 1; k <= SPLITS_PER_LAP; k++) {
    (function (k) {
      var tr = h('tr');
      var th = h('th', null, (k - 1) * SPLIT_M + '–' + k * SPLIT_M + ' m');
      th.scope = 'row';
      tr.appendChild(th);
      tr.appendChild(outputCell('td', 'tools-num', function (s) { return formatSplit(s * SPLIT_M / 1000); }));
      tr.appendChild(outputCell('td', 'tools-num', function (s) { return formatSplit(s * k * SPLIT_M / 1000); }));
      splitBody.appendChild(tr);
    })(k);
  }
  splitTable.appendChild(splitBody);
  p.body.appendChild(splitTable);

  // 多圈時間預設收合（手機上省空間），按「＋」展開
  var toggle = h('button', 'tools-expand', '多圈時間');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  p.body.appendChild(toggle);

  var lapTable = h('table', 'tools-table');
  lapTable.hidden = true;
  var lapBody = h('tbody');
  var labels = [];
  MULTI_LAPS.forEach(function (laps) {
    var tr = h('tr');
    var th = h('th');
    th.scope = 'row';
    labels.push({ node: th, laps: laps });
    tr.appendChild(th);
    tr.appendChild(outputCell('td', 'tools-num', function (s) { return formatSplit(s * lapKm() * laps); }));
    lapBody.appendChild(tr);
  });
  lapTable.appendChild(lapBody);
  p.body.appendChild(lapTable);

  toggle.addEventListener('click', function () {
    var open = lapTable.hidden;
    lapTable.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  function renderLapLabels() {
    labels.forEach(function (l) {
      l.node.textContent = l.laps + ' 圈 · ' + Math.round(tools.trackM * l.laps) + ' m';
    });
  }
  renderLapLabels();
  return p.sec;
}

/** 下拉選項文字：有標註公里數的（10K、半馬、全馬）附在後面，括號同道次下拉用全形。 */
function distanceOptionText(d) {
  return d.label + (d.note ? '（' + d.note + '）' : '');
}

function buildFinishPanel() {
  var p = panel('finish', '完成時間');
  var defaultIndex = RACE_DISTANCES.map(function (d) { return d.km; }).indexOf(tools.finishKm);
  p.body.appendChild(selectField('距離', RACE_DISTANCES.map(distanceOptionText), defaultIndex,
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
  // 外層同首頁／趨勢：全寬面板、標題左側色條，裡面才是淡色標題列的卡片
  var main = h('section', 'panel tools-main');
  var title = h('h2', 'panel-title', '速度換算');
  title.appendChild(h('span', 'panel-hint', '任一格輸入，其他格即時換算'));
  main.appendChild(title);
  var grid = h('div', 'tools-grid');
  grid.appendChild(buildPacePanel());
  grid.appendChild(buildTrackPanel());
  grid.appendChild(buildFinishPanel());
  grid.appendChild(buildTreadmillPanel());
  main.appendChild(grid);
  main.appendChild(h('p', 'tools-note', '時間分格填寫：打完一格按 : 或 Tab 跳下一格，空格視為 0。'));
  body.appendChild(main);
}
