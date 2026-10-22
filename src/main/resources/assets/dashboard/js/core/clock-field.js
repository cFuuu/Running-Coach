/**
 * clock-field.js — 分段式時間輸入：時／分／秒各一格（碼表式），像「5 : 30」。
 *
 * 只負責輸入與顯示，不知道配速或距離——換算由呼叫端（views/tools.js）決定。
 * 值一律以「總秒數」進出：read() 讀出、write() 寫入。
 *
 * 操作：
 *   - 輸入 `:`（或 `.`、全形 `：`）跳到下一格；格內填滿位數也會自動跳格
 *   - 在空格或游標在最前面按 Backspace 回上一格；左右方向鍵在格與格之間穿梭
 *   - 聚焦時整格選取，直接打字就是覆寫
 *   - 可貼上「5:30」「1:45:29」，會自動拆進各格
 *   - 空白的格視為 0，所以只填「分」就是整分鐘
 */

import { h } from './dom.js';
import { parseClock } from './pace.js';

var UNIT_SEC = { h: 3600, m: 60, s: 1 };
var PART_LABEL = { h: '時', m: '分', s: '秒' };
var NEXT_KEYS = [':', ';', '：', '；'];
var MAX_NON_LEADING = 60; // 分、秒（跟在更大單位後面時）不可 ≥ 60，不默默進位

function focusSeg(input, atEnd) {
  input.focus();
  var end = input.value.length;
  if (atEnd) input.setSelectionRange(end, end);
  else input.select();
}

/**
 * @param {Object} opts
 * @param {string[]} opts.parts 例如 ['m','s'] 或 ['h','m','s']
 * @param {string} opts.ariaLabel 整組的名稱（各格會再附上「時／分／秒」）
 * @param {boolean} [opts.decimalSeconds] 最後一格（秒）可帶一位小數（田徑場）
 * @param {boolean} [opts.blankLeadingZero] 第一格為 0 時留白（不顯示「0:04:00」的 0）
 * @param {string[]} [opts.placeholders] 各格的提示字
 * @param {number} [opts.plainUnitSec] 貼上純數字時的單位秒數（60＝分鐘、1＝秒）
 * @param {string} [opts.size] 'lg'｜'md'，只影響 CSS
 * @returns {{node:HTMLElement, read:function, write:function, setInvalid:function,
 *            isInvalid:function, onChange:function, onBlur:function}}
 */
export function clockField(opts) {
  var parts = opts.parts;
  var node = h('span', 'tools-clock' + (opts.size ? ' is-' + opts.size : ''));
  node.setAttribute('role', 'group');
  node.setAttribute('aria-label', opts.ariaLabel);
  var segs = [];
  var changeListeners = [];
  var blurListeners = [];

  function emit() {
    changeListeners.forEach(function (fn) { fn(); });
  }

  parts.forEach(function (part, i) {
    if (i > 0) node.appendChild(h('span', 'tools-colon', ':'));
    var isLast = i === parts.length - 1;
    var decimal = isLast && !!opts.decimalSeconds;

    var input = h('input', 'tools-seg');
    input.type = 'text';
    input.inputMode = decimal ? 'decimal' : 'numeric';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.maxLength = decimal ? 4 : 2;
    input.placeholder = (opts.placeholders || [])[i] || '';
    input.setAttribute('aria-label', opts.ariaLabel + PART_LABEL[part]);
    input.style.width = (input.maxLength + 0.5) + 'ch';

    // 聚焦當下立即全選（不能延遲：點進去馬上打字時，延遲的全選會落在輸入之後），
    // 並攔下緊接著的 mouseup，否則它會把游標放回點擊位置、蓋掉選取
    var selectedByFocus = false;
    input.addEventListener('focus', function () {
      input.select();
      selectedByFocus = true;
    });
    input.addEventListener('mouseup', function (evt) {
      if (!selectedByFocus) return;
      selectedByFocus = false;
      evt.preventDefault();
    });

    input.addEventListener('input', function () {
      var clean = input.value.replace(decimal ? /[^\d.]/g : /\D/g, '');
      if (decimal) clean = clean.replace(/^(\d*\.?)(.*)$/, function (_, head, rest) {
        return head + rest.replace(/\./g, '');
      });
      if (clean !== input.value) input.value = clean;
      emit();
      if (!isLast && input.value.length >= input.maxLength && document.activeElement === input) {
        focusSeg(segs[i + 1]);
      }
    });

    input.addEventListener('keydown', function (evt) {
      var key = evt.key;
      var collapsed = input.selectionStart === input.selectionEnd;
      if (NEXT_KEYS.indexOf(key) !== -1 || (key === '.' && !decimal)) {
        evt.preventDefault();
        if (!isLast) focusSeg(segs[i + 1]);
      } else if (key === 'Backspace' && i > 0 && collapsed && input.selectionStart === 0) {
        evt.preventDefault();
        focusSeg(segs[i - 1], true);
      } else if (key === 'ArrowLeft' && i > 0 && collapsed && input.selectionStart === 0) {
        evt.preventDefault();
        focusSeg(segs[i - 1], true);
      } else if (key === 'ArrowRight' && !isLast && collapsed
          && input.selectionStart === input.value.length) {
        evt.preventDefault();
        focusSeg(segs[i + 1]);
      }
    });

    input.addEventListener('paste', function (evt) {
      var text = evt.clipboardData ? evt.clipboardData.getData('text') : '';
      var total = parseClock(text, opts.plainUnitSec || 60);
      if (total === null) return; // 不是時間格式就照一般貼上，交給 input 的過濾
      evt.preventDefault();
      write(total);
      emit();
    });

    segs.push(input);
    node.appendChild(input);
  });

  node.addEventListener('focusout', function (evt) {
    if (node.contains(evt.relatedTarget)) return;
    blurListeners.forEach(function (fn) { fn(); });
  });

  /** @returns {?number} 總秒數；全空回 null；格式不對回 NaN */
  function read() {
    var raw = segs.map(function (s) { return s.value.trim(); });
    if (raw.every(function (v) { return v === ''; })) return null;
    var total = 0;
    for (var i = 0; i < raw.length; i++) {
      if (raw[i] !== '' && !/^(\d+\.?\d*|\.\d+)$/.test(raw[i])) return NaN;
      var n = raw[i] === '' ? 0 : Number(raw[i]);
      if (i > 0 && n >= MAX_NON_LEADING) return NaN;
      total += n * UNIT_SEC[parts[i]];
    }
    return total > 0 ? total : NaN;
  }

  /** @param {?number} total 總秒數；null＝清空 */
  function write(total) {
    if (total === null) {
      segs.forEach(function (s) { s.value = ''; });
      return;
    }
    // 全程用「0.1 秒」整數運算，避開浮點誤差（例如 59.96 秒要正確進位成 1:00.0）
    var tenths = opts.decimalSeconds ? Math.round(total * 10) : Math.round(total) * 10;
    parts.forEach(function (part, i) {
      var isLast = i === parts.length - 1;
      var text;
      if (isLast && opts.decimalSeconds) {
        var sec = tenths / 10;
        text = (sec < 10 && i > 0 ? '0' : '') + sec.toFixed(1);
      } else {
        var unitTenths = UNIT_SEC[part] * 10;
        var v = Math.floor(tenths / unitTenths);
        tenths -= v * unitTenths;
        text = (i > 0 && v < 10 ? '0' : '') + v;
        if (i === 0 && v === 0 && opts.blankLeadingZero && parts.length > 1) text = '';
      }
      segs[i].value = text;
    });
  }

  return {
    node: node,
    read: read,
    write: write,
    setInvalid: function (on) { node.classList.toggle('is-invalid', !!on); },
    isInvalid: function () { return node.classList.contains('is-invalid'); },
    onChange: function (fn) { changeListeners.push(fn); },
    onBlur: function (fn) { blurListeners.push(fn); }
  };
}
