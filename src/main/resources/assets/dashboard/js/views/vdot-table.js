/**
 * vdot-table.js — VDOT 對照表大視窗（N-41，2026-11-09 Fu 定案）
 *
 * 由首頁跑力卡 ⓘ 的「查看 VDOT 對照表 →」開啟。內容：
 *   ①你的均衡成績：目前 VDOT 的各距離等效成績與 PB 並排、差距
 *   ②等級文字
 *   ③VDOT 20–85 對照表：等效成績＋E／M／T／I 配速，目前所在列標示並自動捲到
 *
 * 等效成績與配速**全部來自 /api/vdot-table**（vdot_engine 的 Daniels 公式與課表固定係數），
 * 前端只負責排版與格式化，不另算任何公式——公式只能有一份。
 */

import * as C from '../charts.js';
import { api } from '../core/api.js';
import { clear, h, showError, showLoading } from '../core/dom.js';
import { openModal } from '../core/modal.js';

var DISTANCE_LABEL = { '3k': '3K', '5k': '5K', '10k': '10K', half_marathon: '半馬', marathon: '全馬' };
// 欄名對應手錶的 %HRR 心率區間（PLAN.md §1 2026-11-09）；不做 R、不做 Z1
var ZONE_LABEL = { easy: 'E·Z2', marathon: 'M·Z3', tempo: 'T·Z4', interval: 'I·Z5' };

// 手機的成績／配速分頁：記住上次看的那一頁（只在本次瀏覽期間）
var activeTab = 'race';

/** 與 scripts/build_dashboard_offline.py vdot_table_request() 同規則（離線快照依此 URL 烘入） */
function tablePath(vdot) {
  return '/api/vdot-table' + (vdot === null || vdot === undefined ? '' : '?vdot=' + Number(vdot).toFixed(1));
}

/**
 * @param {Object} opts
 *   vdot：目前跑力（number；未評為 null）
 *   reason：未評的原因（跑力卡上同一段文字）
 *   personalBests：/api/overview 的 personal_bests
 *   returnFocus：關閉後焦點還給誰（ⓘ 按鈕）
 */
export function openVdotTable(opts) {
  var modal = openModal({ title: 'VDOT 對照表', className: 'vdot-modal', returnFocus: opts.returnFocus });
  showLoading(modal.body, '載入 VDOT 對照表');
  var hasVdot = opts.vdot !== null && opts.vdot !== undefined;
  var outOfRange = false;
  api(tablePath(hasVdot ? opts.vdot : null))
    .catch(function (err) {
      if (!hasVdot) throw err;
      // 後端對超出對照表範圍的 vdot 回 400：仍顯示整張表，只是不標示所在列
      outOfRange = true;
      return api(tablePath(null));
    })
    .then(function (data) {
      if (!modal.isOpen()) return;
      render(modal.body, data, opts, outOfRange);
    })
    .catch(function (err) {
      if (modal.isOpen()) showError(modal.body, err.message);
    });
}

function card(title, hint) {
  var box = h('section', 'vt-card');
  var head = h('h3', 'vt-card-head', title);
  if (hint) head.appendChild(h('span', 'vt-card-hint', hint));
  box.appendChild(head);
  return box;
}

function pbByDistance(personalBests) {
  var out = {};
  (personalBests || []).forEach(function (pb) {
    if (pb.records && pb.records.length) out[pb.distance] = pb.records[0];
  });
  return out;
}

/** PB 減均衡成績（秒）→「慢 1:23」「快 0:45」「持平」 */
function gapText(sec) {
  var s = Math.round(sec);
  if (s === 0) return '持平';
  return (s > 0 ? '慢 ' : '快 ') + C.formatDuration(Math.abs(s));
}

function mineSection(data, opts, outOfRange) {
  var box = card('你的均衡成績', data.current ? 'VDOT ' + C.formatNumber(data.current.vdot, 1) : null);
  var inner = h('div', 'vt-card-body');
  if (!data.current) {
    inner.appendChild(h('p', 'vt-note', outOfRange
      ? '目前跑力 ' + C.formatNumber(opts.vdot, 1) + ' 超出對照表範圍（' + data.vdot_min + '–' + data.vdot_max + '），不標示所在列。'
      : '目前跑力未評' + (opts.reason ? '（' + opts.reason + '）' : '') + '，對照表不標示所在列。'));
    box.appendChild(inner);
    return box;
  }
  var pbs = pbByDistance(opts.personalBests);
  var table = h('table', 'vt-table vt-mine');
  var thead = h('thead');
  var hr = h('tr');
  ['距離', '均衡成績', 'PB', 'PB 比均衡'].forEach(function (t) { hr.appendChild(h('th', null, t)); });
  thead.appendChild(hr);
  table.appendChild(thead);
  var tbody = h('tbody');
  data.distances.forEach(function (key) {
    var eq = data.current.race_times_sec[key];
    var pb = pbs[key];
    var tr = h('tr');
    tr.appendChild(h('th', null, DISTANCE_LABEL[key] || key));
    tr.appendChild(h('td', null, C.formatDuration(eq)));
    tr.appendChild(h('td', null, pb ? C.formatDuration(pb.time_sec) : '—'));
    tr.appendChild(h('td', null, pb ? gapText(pb.time_sec - eq) : '—'));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  inner.appendChild(table);
  inner.appendChild(h('p', 'vt-note',
    '均衡成績是目前跑力在各距離「應該跑得出」的成績。PB 比均衡慢的距離是相對弱項：'
    + '長距離（半馬、全馬）慢，代表有氧耐力還有空間；短距離（3K、5K）慢，代表速度不足。'));
  box.appendChild(inner);
  return box;
}

function tableSection(data, highlight) {
  var box = card('對照表', 'VDOT ' + data.vdot_min + '–' + data.vdot_max);
  var wrap = h('div', 'vt-table-wrap');
  wrap.setAttribute('data-tab', activeTab);

  // 分頁只在手機顯示（CSS）：一次只放成績或配速，欄位才放得下、不橫向捲動
  var tabs = h('div', 'slog-tabs vt-tabs');
  tabs.setAttribute('role', 'tablist');
  var buttons = [];
  [['race', '成績'], ['pace', '配速']].forEach(function (t) {
    var btn = h('button', 'slog-tab' + (t[0] === activeTab ? ' is-active' : ''), t[1]);
    btn.type = 'button';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', t[0] === activeTab ? 'true' : 'false');
    btn.addEventListener('click', function () {
      activeTab = t[0];
      wrap.setAttribute('data-tab', activeTab);
      buttons.forEach(function (b) {
        var on = b === btn;
        b.classList.toggle('is-active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
      });
    });
    buttons.push(btn);
    tabs.appendChild(btn);
  });
  wrap.appendChild(tabs);

  var table = h('table', 'vt-table vt-full');
  var thead = h('thead');
  var hr = h('tr');
  hr.appendChild(h('th', null, 'VDOT'));
  data.distances.forEach(function (key) { hr.appendChild(h('th', 'vt-race', DISTANCE_LABEL[key] || key)); });
  data.pace_zones.forEach(function (key) { hr.appendChild(h('th', 'vt-pace', ZONE_LABEL[key] || key)); });
  thead.appendChild(hr);
  table.appendChild(thead);

  var tbody = h('tbody');
  var currentRow = null;
  data.rows.forEach(function (row) {
    var tr = h('tr');
    if (row.vdot === highlight) {
      tr.className = 'is-current';
      tr.setAttribute('aria-current', 'true');
      currentRow = tr;
    }
    tr.appendChild(h('th', null, C.formatNumber(row.vdot, 0)));
    data.distances.forEach(function (key) {
      tr.appendChild(h('td', 'vt-race', C.formatDuration(row.race_times_sec[key])));
    });
    data.pace_zones.forEach(function (key) {
      var z = row.paces[key];
      tr.appendChild(h('td', 'vt-pace', C.formatPace(z.fast_sec_per_km) + '–' + C.formatPace(z.slow_sec_per_km)));
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  wrap.appendChild(h('p', 'vt-note vt-table-note',
    '等效成績用 Daniels 公式由 VDOT 反推；配速為課表使用的固定係數（以全馬配速為基準），單位 /km。'
    + 'Z2–Z5 對應手錶的心率區間。VDOT 30 以下為公式外推，數字較不準。'));
  box.appendChild(wrap);
  return { node: box, currentRow: currentRow };
}

function render(body, data, opts, outOfRange) {
  clear(body);
  body.appendChild(mineSection(data, opts, outOfRange));
  body.appendChild(h('p', 'vt-level', '等級參考：入門約 30 以內、40 以上進階、50 以上高手。'));
  // 所在列＝目前 VDOT 落在的那一格（42.9 在 42 那一列，還沒到 43）
  var highlight = data.current ? Math.floor(data.current.vdot) : null;
  var table = tableSection(data, highlight);
  body.appendChild(table.node);

  if (table.currentRow) {
    // 等排版完成再捲：把所在列捲到捲動區中間（捲的是視窗內容區，不是頁面）
    requestAnimationFrame(function () {
      var row = table.currentRow.getBoundingClientRect();
      var box = body.getBoundingClientRect();
      body.scrollTop += (row.top - box.top) - (box.height - row.height) / 2;
    });
  }
}
