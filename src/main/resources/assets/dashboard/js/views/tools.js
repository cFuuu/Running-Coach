/**
 * tools.js — 配速計算機（#/tools）：配速／跑步機／田徑場／完成時間四區同頁連動
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

// 田徑場表格的列：一圈的幾分之幾（1 圈以下是 100／200／300m 分段）
var LAP_FRACTIONS = [0.25, 0.5, 0.75, 1, 2, 3, 4, 5];

var tools = {
  built: false,
  secPerKm: null,
  trackM: TRACK_OPTIONS[0].meters,
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

function numberInput(placeholder, ariaLabel) {
  var input = h('input', 'tools-input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = placeholder;
  input.setAttribute('aria-label', ariaLabel);
  return input;
}

function labeledField(label, unit, control, extraClass) {
  var wrap = h('div', 'tools-field' + (extraClass ? ' ' + extraClass : ''));
  wrap.appendChild(h('span', 'tools-field-label', label));
  var row = h('span', 'tools-field-row');
  row.appendChild(control);
  if (unit) row.appendChild(h('span', 'tools-unit', unit));
  wrap.appendChild(row);
  return wrap;
}

function panel(title, hint, area) {
  var sec = h('section', 'panel tools-panel tools-area-' + area);
  var h2 = h('h2', 'panel-title', title);
  if (hint) h2.appendChild(h('span', 'panel-hint', hint));
  sec.appendChild(h2);
  return sec;
}

// ------------------------------------------------------------ 四個區塊

function buildPacePanel() {
  var sec = panel('配速', null, 'pace');
  var row = h('div', 'tools-hero-row');
  row.appendChild(labeledField('每公里', '/km', bindClock(
    clockField({ parts: ['m', 's'], ariaLabel: '每公里配速', placeholders: ['5', '30'], plainUnitSec: 60, size: 'lg' }),
    function (s) { return s; },
    function (t) { return t; }
  ), 'is-hero'));
  row.appendChild(labeledField('每英里', '/mi', bindClock(
    clockField({ parts: ['m', 's'], ariaLabel: '每英里配速', placeholders: ['8', '51'], plainUnitSec: 60, size: 'lg' }),
    function (s) { return s * KM_PER_MILE; },
    function (t) { return t / KM_PER_MILE; }
  ), 'is-hero'));
  sec.appendChild(row);
  return sec;
}

function buildTreadmillPanel() {
  var sec = panel('跑步機', null, 'treadmill');
  var grid = h('div', 'tools-fields');
  grid.appendChild(labeledField('公里時速', 'km/h', bindNumber(
    numberInput('10.9', '公里時速'),
    function (s) { return paceToKmh(s).toFixed(2); },
    function (v) { return kmhToPace(v); }
  )));
  grid.appendChild(labeledField('英里時速', 'mph', bindNumber(
    numberInput('6.78', '英里時速'),
    function (s) { return (paceToKmh(s) / KM_PER_MILE).toFixed(2); },
    function (v) { return kmhToPace(v * KM_PER_MILE); }
  )));
  sec.appendChild(grid);
  return sec;
}

function buildTrackPanel() {
  var sec = panel('田徑場', '換道時速度不變，重算每圈時間', 'track');
  var controls = h('div', 'tools-fields');

  var select = h('select', 'tools-input tools-select');
  select.setAttribute('aria-label', '道次');
  TRACK_OPTIONS.forEach(function (opt, i) {
    var o = h('option', null, opt.label + '（' + Number(opt.meters.toFixed(1)) + 'm）');
    o.value = String(i);
    select.appendChild(o);
  });
  controls.appendChild(labeledField('道次', null, select));

  controls.appendChild(labeledField('每圈', null, bindClock(
    clockField({
      parts: ['m', 's'], ariaLabel: '每圈時間', decimalSeconds: true, blankLeadingZero: true,
      placeholders: ['0', '96'], plainUnitSec: 1
    }),
    function (s) { return s * tools.trackM / 1000; },
    function (t) { return t / (tools.trackM / 1000); }
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
  var sec = panel('完成時間', '照目前配速等速跑完各距離', 'finish');
  var table = h('table', 'tools-table');
  var tbody = h('tbody');
  RACE_DISTANCES.forEach(function (d) {
    var tr = h('tr');
    var th = h('th', null, d.label);
    th.scope = 'row';
    tr.appendChild(th);
    var td = h('td');
    td.appendChild(bindClock(
      clockField({
        parts: ['h', 'm', 's'], ariaLabel: d.label + '完成時間', blankLeadingZero: true,
        placeholders: ['0', '00', '00'], plainUnitSec: 60, size: 'md'
      }),
      function (s) { return s * d.km; },
      function (t) { return t / d.km; }
    ));
    tr.appendChild(td);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  sec.appendChild(table);
  return sec;
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
  grid.appendChild(buildTreadmillPanel());
  grid.appendChild(buildTrackPanel());
  grid.appendChild(buildFinishPanel());
  body.appendChild(grid);
}
