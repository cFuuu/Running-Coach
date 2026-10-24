/**
 * home.js — 首頁 view（2026-10-21 由「今日」與「總覽」合併）
 *
 * 固定看「現在」，不吃頂列的時間範圍——會隨範圍變動的走勢都在趨勢頁。
 * 6 個區塊，依「回答什麼問題」分（分類見 docs/dev/FRONTEND_REFACTOR.md 階段 6）：
 *   綜覽（今日課表＋綜合整備狀態／本週訓練狀況＋賽事倒數）、訓練負荷、體能狀況、
 *   當週課表、戰力綜覽、近期活動
 * 區塊的順序與顯隱由使用者自訂（core/customize.js，偏好 key 'home'）。
 *
 * 純呈現層：把三欄位事實（數值/來源/信賴度）與課表原樣顯示出來，
 * 不做任何「今天該不該練」的判讀——判讀留給 AI 教練（見 PLAN.md §7.3、
 * mcp/server.py 的 coach 行為約束）。各指標的 ⓘ 只寫定義與算法。
 */

import * as C from '../charts.js';
import { state, WORKOUT_TYPE_LABEL, ACTIVITY_TYPE_LABEL, DAY_STATE_LABEL, CONFIDENCE_LABEL, HRV_STATUS_LABEL } from '../core/state.js';
import { api, apiSend, isOfflineSnapshot } from '../core/api.js';
import { $, clear, h, showLoading, showError, showUnavailable } from '../core/dom.js';
import { confBadge, kpiCard, infoTip, closeInfoPopover, hrvStatusMark } from '../core/facts.js';
import { addDaysISO, todayISO, dowLabel, shortPlanNote, warmupsByMain, warmupLabel, raceDisplayName, displayDate } from '../core/format.js';
import { customizer, layoutOf } from '../core/customize.js';
import { loadPlanWeek, planWeekLabel, renderPlanWeek, planWeekTile, planWeekTileValue, planWeekSummary } from './plan-week.js';
import { activityDots, firstRunningActivity } from './calendar.js';
import { renderSettingsSync } from './settings.js';
import { zoneSplitBar } from './overview.js';
import { renderGarminWall, renderGarminCard } from './garmin.js';
import { navigate } from '../shell/router.js';
import { renderSidebarSync } from '../shell/sync.js';

// ------------------------------------------------------------ 區塊版面

var HOME_BLOCKS = [
  { key: 'today', label: '綜覽' },
  { key: 'load', label: '訓練負荷' },
  { key: 'body', label: '體能狀況' },
  { key: 'week', label: '當週課表' },
  { key: 'ability', label: '戰力綜覽' },
  { key: 'recent', label: '近期活動' }
];

/**
 * 依使用者偏好重排區塊（移動既有 DOM，不重畫內容）並套用顯隱。
 * 任何區塊都能並排：可見區塊由上而下兩兩一組，各佔半欄；最後落單的佔滿整欄，
 * 避免半邊空白。區塊內容依自身寬度（container query）調整欄數，半欄也排得下。
 */
export function applyHomeLayout() {
  var grid = $('home-grid');
  if (!grid) return;
  var layout = layoutOf('home', HOME_BLOCKS);
  var visible = [];
  layout.ordered.forEach(function (b) {
    var el = $('block-' + b.key);
    if (!el) return;
    grid.appendChild(el);
    el.hidden = !!layout.hidden[b.key];
    el.classList.remove('is-half');
    if (!el.hidden) visible.push(b);
  });
  for (var i = 0; i + 1 < visible.length; i += 2) {
    $('block-' + visible[i].key).classList.add('is-half');
    $('block-' + visible[i + 1].key).classList.add('is-half');
  }
}

function mountHomeCustomizer() {
  var node = $('home-customize');
  if (!node) return;
  clear(node);
  node.appendChild(customizer({
    settingsKey: 'home',
    defs: HOME_BLOCKS,
    label: '⚙ 自訂首頁',
    hint: '拖移右側的 ⠿ 調整區塊順序，取消勾選可隱藏。區塊由上而下兩兩並排，落單的佔滿整欄。',
    onChange: function () {
      applyHomeLayout();
      mountHomeCustomizer();
    }
  }));
}

/** 啟動時呼叫一次：套用區塊順序並放上「自訂首頁」鈕。 */
export function initHomeLayout() {
  applyHomeLayout();
  mountHomeCustomizer();
}

// ------------------------------------------------------------ 今天：課表＋賽事倒數＋綜合整備狀態

/**
 * 一天的課表：課別＋距離、配速（粗體），後面接教練原文的精簡版（淡色，完整原文在 title）。
 * 休息日只顯示「休息」——備註只是「Rest」之類的重述（與 plan-week.js 一致）。
 */
export function renderTodayPlanRow(label, plan) {
  var row = h('div', 'today-plan-row');
  row.appendChild(h('span', 'today-plan-label', label));
  if (!plan) {
    row.appendChild(h('span', 'today-plan-value', '無課表'));
    return row;
  }
  var text = (WORKOUT_TYPE_LABEL[plan.workout_type] || plan.workout_type || '—');
  if (plan.planned_distance_km !== null && plan.planned_distance_km !== undefined && plan.planned_distance_km > 0) {
    text += ' ' + C.formatNumber(plan.planned_distance_km, 1) + ' km';
  }
  row.appendChild(h('span', 'today-plan-value', text));
  if (plan.planned_pace_sec_per_km) {
    row.appendChild(h('span', 'today-plan-value', C.formatPace(plan.planned_pace_sec_per_km) + '/km'));
  }
  var note = plan.workout_type === 'rest' ? '' : shortPlanNote(plan.notes);
  // 教練原文開頭的「課程日」只是標籤，移到句尾，讓課表內容緊接在配速後面
  var tag = note.match(/^課程日\s*/);
  if (tag) note = note.slice(tag[0].length) + ' 課程日';
  if (note) {
    var n = h('span', 'today-plan-note', note);
    n.title = plan.notes;
    row.appendChild(n);
  }
  return row;
}

/** 課表標題：「課表（於 YYYY-MM-DD 更新）」，取今日或明日課表的建立日期。 */
function planBlockTitle(planned) {
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '課表'));
  var source = planned.today || planned.tomorrow;
  if (source && source.created_at) {
    title.appendChild(h('span', 'today-note', '（於' + displayDate(source.created_at.slice(0, 10)) + '更新）'));
  }
  return title;
}

// 判定 → 顯示。顏色之外一律附圖示與文字，不單靠顏色（色盲、列印也看得懂）。
var READINESS_STATE = {
  normal: { cls: 'ok', icon: '✓', text: '良好' },
  low: { cls: 'low', icon: '▼', text: '低落' },
  // 本專案規則都沒觸發、但 Garmin 健康狀態有指標在範圍外時才顯示（後端 readiness 仍是 normal）
  caution: { cls: 'caution', icon: '!', text: '注意' },
  none: { cls: 'none', icon: '–', text: '未評' }
};

function readinessStateOf(value) {
  return READINESS_STATE[value === 'low' || value === 'normal' || value === 'caution' ? value : 'none'];
}

// Garmin 健康狀態卡的指標名稱與方向（範圍與判定都是 Garmin 給的，這裡只翻譯字眼）
var GARMIN_HEALTH_METRIC_LABEL = {
  HR: '靜止心率', HRV: 'HRV', RESPIRATION: '呼吸率', SPO2: '血氧', SKIN_TEMP_C: '皮膚溫度'
};
var GARMIN_HEALTH_STATUS_LABEL = { BELOW: '低於範圍', ABOVE: '高於範圍' };

/** Garmin 健康狀態中範圍外的指標（今日 API 的 garmin_reference）；沒有資料回傳空陣列。 */
function garminHealthOutliers() {
  var ref = state.today && state.today.garmin_reference;
  return (ref && ref.caution && ref.health_status && ref.health_status.outliers) || [];
}

/** 「HRV 低於範圍、呼吸率 高於範圍」這種一句話，注意狀態的原因說明用。 */
function garminOutlierText(outliers) {
  return outliers.map(function (o) {
    return (GARMIN_HEALTH_METRIC_LABEL[o.metric] || o.metric) + ' '
      + (GARMIN_HEALTH_STATUS_LABEL[o.status] || o.status);
  }).join('、');
}

function readinessPill(value) {
  var s = readinessStateOf(value);
  var pill = h('span', 'readiness-pill readiness-' + s.cls);
  pill.appendChild(h('span', 'readiness-pill-icon', s.icon));
  pill.appendChild(h('span', null, s.text));
  return pill;
}

var READINESS_TIPS = [
  '綜合整備狀態是本專案規則引擎（公開、可重現的規則，不是 AI 判讀）'
  + '對「今天身體恢復得夠不夠完備」的判定。顏色一看就懂：'
  + '綠色 ✓ 良好（查過了，沒有異常訊號）；紅色 ▼ 低落（至少一個維度觸發）；'
  + '橘色 ! 注意（本專案的規則都沒觸發，但 Garmin 手錶的健康狀態有指標落在範圍外）；'
  + '灰色 – 未評（維度都缺資料、無法評估，不等於良好）。',
  '兩個維度，任一觸發就判為「低落」：①TSB 低於你自己歷史分布的第 10 百分位；'
  + '②當日 HRV 較 7 日均值下降 15% 以上（這個 7 日均值是 Garmin 提供的，不是本專案算的）。'
  + '另有「連續訓練」規則（連續訓練達 6 天，預設值可個人化），平常不顯示，觸發時才會多一列。',
  '各觸發項旁的標籤是信賴度：TSB 取決於最大心率是否實測；HRV 的百分比門檻尚未經文獻驗證，標為低。',
  '資料不足的維度會跳過（例如 HRV 近 7 天少於 3 筆、當天尚未同步）。',
  'HRV 變化列尾的橘色「趨勢偏低」是另一條規則，只是提醒，不計入判定：HRV 7 日平均低於你前 4 週基線'
  + '（ln 尺度 mean − 0.5×SD）時出現。它補的是 15% 規則抓不到的慢性下滑，'
  + '但本來就約有四分之一的日子會落在線下，而且不知道原因（熬夜、飲酒、生病也會觸發）。'
  + '滑鼠移到 HRV 變化列上可看趨勢的數字。',
  '「注意」看的是 Garmin 手錶的「健康狀態」：靜止心率、HRV、呼吸率、血氧四項，'
  + '各自對照手錶依你個人歷史算出的範圍，任何一項落在範圍外就觸發（其中 HRV 就是這個健康狀態裡的 HRV）。'
  + '演算法未公開，不是本專案的規則，不會讓狀態變「低落」，只在其他規則都沒觸發時，把「良好」改成「注意」。'
  + '卡片下方會列出是哪幾項。AI 教練讀得到這份資料，會標明是 Garmin 參考，也不會單憑它建議休息。',
  '「HRV 狀態」是 Garmin 手錶（Firstbeat）給的另一個 HRV 分類（平衡／不平衡／偏低／不佳）：'
  + '把你的 HRV 7 日均值，對照手錶自己算出的個人平衡區間所得到的結果。只供顯示，不影響判定。'
  + '滑鼠移上去可看 7 日均值與平衡區間。',
  '這裡只給旗標與原因，不改課表也不給建議；要不要調整交給 AI 教練。'
  + '與 Garmin 的「訓練完備程度」是兩回事，後者是 Garmin 數據，不納入本專案判斷。'
];

/** 今天還沒同步、改顯示較早一天資料時的說明文字。 */
function unsyncedNote(dateStr) {
  return '今日資料尚未同步，以下為 ' + C.shortDate(dateStr) + '（' + dowLabel(dateStr) + '）的資料';
}

/** 帶正負號的數字（正數補「+」），TSB 與 HRV 變化用。 */
function signedNumber(v, decimals) {
  return (v > 0 ? '+' : '') + C.formatNumber(v, decimals);
}

// 判斷維度，依「數值 / 觸發門檻」並列（與本週訓練狀況的「實際 / 課表」同一種讀法）。
// key 對應後端 readiness 的 measurements，dim 對應 triggers／not_assessed 的 dimension。
// 連續訓練規則仍計入判定，但以學員的訓練頻率幾乎不會觸發，平常不佔一列，
// 觸發時才補上（否則會出現「低落」卻看不到原因）。
var CONSECUTIVE_ROW = {
  dim: 'consecutive_training_days', key: 'consecutive_training_days', label: '連續訓練',
  fmt: function (v) { return String(v); }, unit: ' 天'
};

var READINESS_ROWS = [
  {
    dim: 'tsb', key: 'tsb', label: 'TSB 指標',
    fmt: function (v) { return signedNumber(v, 1); }, unit: ''
  },
  {
    dim: 'hrv', key: 'hrv_change_pct', label: 'HRV 變化',
    fmt: function (v) { return signedNumber(v, 1) + '%'; },
    // 門檻是固定的 −15，不需要小數，省下寬度
    fmtThreshold: function (v) { return signedNumber(v, 0) + '%'; }, unit: ''
  }
];

/** 一列「數值 / 門檻」。觸發的維度整列標紅並附信賴度；未評的顯示「—」，滑鼠移上去看原因。 */
function readinessRow(def, entry) {
  var m = entry && entry.measurements && entry.measurements[def.key];
  var trigger = entry && (entry.triggers || []).filter(function (t) { return t.dimension === def.dim; })[0];
  var skipped = entry && (entry.not_assessed || []).filter(function (na) { return na.dimension === def.dim; })[0];
  var hasValue = m && m.value !== null && m.value !== undefined;

  var row = h('div', 'wt-row rd-row' + (trigger ? ' is-triggered' : ''));
  row.appendChild(h('span', 'wt-row-label', def.label));
  var val = h('span', 'wt-row-value');
  val.appendChild(h('span', 'rd-value', hasValue ? def.fmt(m.value) : '—'));
  if (hasValue && m.threshold !== null && m.threshold !== undefined) {
    val.appendChild(h('span', 'wt-planned', '/ ' + (def.fmtThreshold || def.fmt)(m.threshold) + def.unit));
  } else if (hasValue && def.unit) {
    val.appendChild(h('span', 'wt-planned', def.unit.trim()));
  }
  row.appendChild(val);
  var tips = [];
  if (trigger) {
    var level = (trigger.confidence && trigger.confidence.level) || 'high';
    row.appendChild(h('span', 'conf-badge conf-' + level, CONFIDENCE_LABEL[level] || level));
    tips.push(trigger.detail);
  } else if (skipped) {
    tips.push(skipped.reason);
  }
  // HRV 列另帶「趨勢（SWC）」提醒：只是提醒，與判定無關，偏低時才在列尾出現標籤，數字放滑鼠提示
  var trend = def.dim === 'hrv' && entry && entry.hrv_trend;
  if (trend) {
    if (trend.status === 'below') row.appendChild(h('span', 'conf-badge conf-reference', '趨勢偏低'));
    tips.push('HRV 趨勢：' + (trend.detail || trend.reason || '')
      + (trend.detail ? '（僅提醒，不計入判定）' : ''));
  }
  if (tips.length) row.title = tips.join('\n');
  return row;
}

/**
 * Garmin（Firstbeat）的 HRV 狀態列：手錶給的結果，只供對照，不計入判定、也不會標紅。
 * 資料來自今日 API 的 hrv.garmin_reference（for_display_only），分類文字與配色沿用 HRV 卡片。
 */
function garminHrvRow() {
  var hrv = state.today && state.today.hrv;
  var ref = hrv && hrv.garmin_reference;
  var status = ref && ref.status;
  var row = h('div', 'wt-row rd-row');
  row.appendChild(h('span', 'wt-row-label', 'HRV 狀態'));
  // 圖示與文字以中線對齊（其他列的數值是以基線對齊）
  var val = h('span', 'wt-row-value is-centered');
  var mark = hrvStatusMark(status);
  if (mark) val.appendChild(mark);
  val.appendChild(h('span', 'rd-value rd-value-text', status ? (HRV_STATUS_LABEL[status] || status) : '—'));
  row.appendChild(val);

  var tips = ['Garmin（Firstbeat）判定的 HRV 狀態，演算法未公開，不屬於本專案的判定規則'];
  var avg = hrv && hrv.weekly_avg_ms && hrv.weekly_avg_ms.value;
  if (avg !== null && avg !== undefined) tips.push('7 日均值 ' + C.formatNumber(avg, 1) + ' ms');
  if (ref && ref.baseline_balanced_low && ref.baseline_balanced_upper) {
    tips.push('平衡區間 ' + C.formatNumber(ref.baseline_balanced_low, 0) + '–'
      + C.formatNumber(ref.baseline_balanced_upper, 0) + ' ms');
  }
  if (!status) tips.push('尚無 Garmin HRV 狀態');
  if (hrv && hrv.as_of) tips.push('資料日期 ' + hrv.as_of);
  row.title = tips.join('\n');
  return row;
}

// 每日自評體感（3 級，由累到好），值對應後端 daily_feel_store.FEEL_LEVELS
// icon 是單色線條臉（currentColor，選中時跟著變白）：疲憊＝嘴角向下、普通＝嘴角平、充沛＝嘴角向上。
// 文字只放在滑鼠提示與 aria-label，按鈕本身不佔字寬。內容是固定字面值，不含外部資料。
var FEEL_EYES = '<circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/>'
  + '<circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/>';
var FEEL_OPTIONS = [
  { value: 'tired', label: '疲憊', icon: FEEL_EYES + '<path d="M8.5 16.5c1-1.6 2.2-2.3 3.5-2.3s2.5.7 3.5 2.3"/>' },
  { value: 'normal', label: '普通', icon: FEEL_EYES + '<path d="M8.5 15.5h7"/>' },
  {
    value: 'energetic', label: '充沛',
    icon: FEEL_EYES + '<path d="M8 14c1 1.8 2.3 2.7 4 2.7s3-.9 4-2.7"/>'
  }
];

var FEEL_TIPS = [
  '今日體感狀況只是記錄，不影響任何判定（綜合整備狀態、訓練負荷都不讀它）。'
  + '累積下來用來檢驗規則抓到的日子是不是真的累。',
  '點一下記錄，再點同一顆取消。記的是「今天」，即使上方因資料未同步而顯示較早一天的判定。'
];

/**
 * 「今日體感狀況」獨立區塊：三段式選擇，點一下記錄、再點同一段取消。
 * 只是記錄供日後對照規則準不準，不影響任何判定（後端規則模組不讀這張表）。
 */
function renderFeelBlock(dateStr) {
  var block = h('div', 'today-block feel-block');
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '今日體感狀況'));
  title.appendChild(infoTip('今日體感狀況', FEEL_TIPS));
  block.appendChild(title);
  block.appendChild(h('div', 'feel-sub', '自行評估今天身體狀況'));

  var group = h('div', 'feel-options');
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', '今日體感狀況');
  var current = null;
  var buttons = FEEL_OPTIONS.map(function (opt) {
    var btn = h('button', 'feel-btn feel-' + opt.value);
    btn.type = 'button';
    btn.title = opt.label;
    btn.setAttribute('aria-label', opt.label);
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" '
      + 'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
      + '<circle cx="12" cy="12" r="9"/>' + opt.icon + '</svg>';
    btn.addEventListener('click', function () { save(opt.value === current ? null : opt.value); });
    group.appendChild(btn);
    return btn;
  });
  block.appendChild(group);
  // 狀態文字在圖示下方，永遠佔一行高度，記錄／取消時圖示不會跳動
  var status = h('span', 'feel-status');
  block.appendChild(status);

  function paint() {
    FEEL_OPTIONS.forEach(function (opt, i) {
      var on = opt.value === current;
      buttons[i].classList.toggle('is-active', on);
      buttons[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function save(value) {
    var path = '/api/daily-feel/' + dateStr;
    var req = value ? apiSend('PUT', path, { feel: value }) : apiSend('DELETE', path);
    status.textContent = '儲存中…';
    req.then(function () {
      current = value;
      paint();
      status.textContent = value ? '已記錄' : '已取消';
    }).catch(function (err) {
      status.textContent = '儲存失敗：' + err.message;
    });
  }

  api('/api/daily-feel?start=' + dateStr + '&end=' + dateStr).then(function (data) {
    var feels = (data && data.feels) || [];
    current = feels.length ? feels[0].feel : null;
    paint();
  }).catch(function () { /* 讀不到就當沒記過，按鈕照樣可用 */ });

  return block;
}

// 判定圖示：形狀＋單色線條圖示（與 HRV 狀態的綠圓／橘方／紅三角同一套語言，色盲也分得出來）。
// 良好＝圓＋勾、注意＝圓角方＋驚嘆號、低落＝三角＋向下箭頭、未評＝虛線圓＋橫線。
// 內容是固定字面值，不含外部資料；圖示畫在 24×24，再平移縮放到 60×60 的形狀中央。
var RD_ICON = {
  check: '<path d="M6 12.5l4.2 4.2L18 8"/>',
  bang: '<path d="M12 6.5v7"/><circle class="rd-dot" cx="12" cy="17.6" r="1.3"/>',
  down: '<path d="M12 6v11.5"/><path d="M6.8 12.6L12 17.8l5.2-5.2"/>',
  dash: '<path d="M7.5 12h9"/>'
};
var RD_SHAPE = {
  ok: '<circle class="rd-shape" cx="30" cy="30" r="27"/>'
    + '<g class="rd-ico" transform="translate(15 15) scale(1.25)">' + RD_ICON.check + '</g>',
  caution: '<rect class="rd-shape" x="3" y="3" width="54" height="54" rx="12"/>'
    + '<g class="rd-ico" transform="translate(15 15) scale(1.25)">' + RD_ICON.bang + '</g>',
  low: '<path class="rd-shape" d="M30 4 L57 52 H3 Z"/>'
    + '<g class="rd-ico" transform="translate(18 21)">' + RD_ICON.down + '</g>',
  none: '<circle class="rd-shape rd-shape-dashed" cx="30" cy="30" r="27"/>'
    + '<g class="rd-ico" transform="translate(15 15) scale(1.25)">' + RD_ICON.dash + '</g>'
};

/** 右側的判定圖示：形狀＋圖示，下方一行判定文字。 */
function readinessStatus(value) {
  var s = readinessStateOf(value);
  var badge = h('span', 'rd-status rd-' + s.cls);
  badge.innerHTML = '<svg viewBox="0 0 60 60" width="60" height="60" aria-hidden="true">'
    + RD_SHAPE[s.cls] + '</svg>';
  badge.appendChild(h('span', 'rd-status-label', s.text));
  if (s.cls === 'none') badge.title = '各維度都缺資料，無法評估（不等於良好）';
  else if (s.cls === 'caution') badge.title = '本專案規則都沒觸發，但 Garmin 健康狀態有指標在範圍外：' + garminOutlierText(garminHealthOutliers());
  else badge.title = '綜合整備狀態：' + s.text;
  return badge;
}

export function renderTodayReadinessBlock(readiness) {
  var block = h('div', 'today-block readiness-block fig-block');
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '綜合整備狀態'));
  block.appendChild(title);

  var entry = readiness && readiness.today;
  var fallback = readiness && readiness.today_fallback;
  var todayMissing = !entry || entry.readiness === null || entry.readiness === undefined;
  var unsynced = false;
  if (todayMissing && fallback) {
    // 今天還沒同步：先呈現退回窗口內最近一天的判定，日期標在標題旁、說明放在卡片下方
    unsynced = true;
    entry = fallback;
    todayMissing = false;
  }
  var shownDate = entry && entry.date ? entry.date : (state.today && state.today.date);
  if (shownDate) title.appendChild(h('span', 'today-note fig-title-date', C.shortDate(shownDate) + ' (' + dowLabel(shownDate) + ')'));
  // ⓘ 靠標題列最右邊，所以要在日期之後才加
  title.appendChild(infoTip('綜合整備狀態', READINESS_TIPS));

  // 左側三個維度的數值 / 門檻、右側判定圖示
  var card = h('div', 'wt-card');
  var rows = h('div', 'wt-figs');
  var shown = todayMissing ? null : entry;
  var consecutiveTriggered = shown && (shown.triggers || []).some(function (t) {
    return t.dimension === CONSECUTIVE_ROW.dim;
  });
  if (consecutiveTriggered) rows.appendChild(readinessRow(CONSECUTIVE_ROW, shown));
  READINESS_ROWS.forEach(function (def) { rows.appendChild(readinessRow(def, shown)); });
  rows.appendChild(garminHrvRow());
  card.appendChild(rows);
  // 本專案規則都沒觸發（良好）、卻只有 Garmin 健康狀態有指標在範圍外時，改顯示橘色「注意」
  var shownState = todayMissing ? null : entry.readiness;
  var outliers = garminHealthOutliers();
  if (shownState === 'normal' && outliers.length) shownState = 'caution';
  card.appendChild(readinessStatus(shownState));
  block.appendChild(card);
  if (shownState === 'caution') {
    block.appendChild(h('div', 'sync-fallback-note', 'Garmin 健康狀態：' + garminOutlierText(outliers)));
  }

  if (unsynced) block.appendChild(h('div', 'sync-fallback-note is-muted', '今日資料尚未同步'));
  if (todayMissing) {
    var reasons = (entry && entry.not_assessed || []).map(function (na) { return na.reason; });
    block.appendChild(h('div', 'today-note wt-note', reasons.length ? reasons[0] : '今日尚無足夠資料可評估'));
    var latest = readiness && readiness.latest_evaluable;
    if (latest) {
      var lrow = h('div', 'today-note');
      lrow.appendChild(document.createTextNode('最近一次可評估：' + displayDate(latest.date) + '　'));
      lrow.appendChild(readinessPill(latest.readiness));
      block.appendChild(lrow);
    }
  }
  return block;
}

/** 「下場賽事」「目標賽事」剩餘天數。賽事資料來自學員檔（後端 races）。 */
// 單色線條圖示，用 currentColor 跟著文字變色。內容是固定的字面值，不含任何外部資料。
var RACE_ICON_SVG = {
  // 標靶：三圈同心圓加靶心
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/>',
  // 旗子：旗桿加旗面
  flag: '<path d="M6 21V4"/><path d="M6 5h11l-2.5 3.5L17 12H6"/>'
};

function raceIcon(kind) {
  var span = h('span', 'race-icon');
  span.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" '
    + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + RACE_ICON_SVG[kind] + '</svg>';
  return span;
}

// 賽事距離分級（公里）：全馬 42K 與 42.195K 都算全馬，超過 42.5K 才是超馬
var RACE_TIER_ULTRA_KM = 42.5;
var RACE_TIER_FULL_KM = 42;
var RACE_TIER_HALF_KM = 21;
var RACE_TIER_TEN_KM = 10;

/** 距離 → 顏色級別：ultra 紫、full 紅、half 橘、ten 綠（10K～半馬以下）、short 藍（10K 以下）。 */
function raceDistanceTier(km) {
  if (km > RACE_TIER_ULTRA_KM) return 'ultra';
  if (km >= RACE_TIER_FULL_KM) return 'full';
  if (km >= RACE_TIER_HALF_KM) return 'half';
  if (km >= RACE_TIER_TEN_KM) return 'ten';
  return 'short';
}

/** 剩餘訓練週數（無條件進位）；7 天內就是賽前最後一週。 */
function raceWeeksText(daysLeft) {
  var weeks = Math.ceil(daysLeft / 7);
  return daysLeft <= 7 ? '賽前' + weeks + '週' : '剩餘' + weeks + '週';
}

function raceCard(iconKind, label, race, extraClass) {
  var card = h('div', 'race-card' + (extraClass ? ' ' + extraClass : ''));
  var labelRow = h('div', 'race-card-label');
  labelRow.appendChild(raceIcon(iconKind));
  labelRow.appendChild(h('span', null, label));
  card.appendChild(labelRow);
  if (!race) {
    card.appendChild(h('div', 'today-note', '尚未設定'));
    return card;
  }
  var days = h('div', 'race-card-days');
  days.appendChild(h('span', 'race-days', race.days_left === 0 ? '比賽日' : String(race.days_left)));
  if (race.days_left !== 0) {
    days.appendChild(h('span', 'race-days-unit', '天'));
    days.appendChild(h('span', 'race-weeks', '/ ' + raceWeeksText(race.days_left)));
  }
  card.appendChild(days);
  card.appendChild(raceNameRow(race, 'race-card-name'));
  card.appendChild(h('div', 'today-note', displayDate(race.date)));
  return card;
}

/** 賽事名稱（去掉西元年，完整名稱放 title）＋距離標籤。距離接在名稱後面、字級相同，只靠顏色分級。 */
function raceNameRow(race, className) {
  var nameRow = h('div', className, raceDisplayName(race.name));
  if (race.name) nameRow.title = race.name;
  if (race.distance) {
    nameRow.appendChild(document.createTextNode(' '));
    nameRow.appendChild(h('span', 'race-distance race-dist-' + raceDistanceTier(race.distance_km), race.distance));
  }
  return nameRow;
}

/** 「全部賽事」清單：所有未過期賽事依日期排列，目標賽事以中性的「目標」標記。 */
function raceListPanel(all) {
  var list = h('div', 'race-list');
  all.forEach(function (race) {
    var row = h('div', 'race-list-row' + (race.is_target ? ' is-target' : ''));
    row.appendChild(h('span', 'race-list-date', displayDate(race.date)));
    var name = raceNameRow(race, 'race-list-name');
    if (race.is_target) name.appendChild(h('span', 'race-target-tag', '目標'));
    row.appendChild(name);
    var days = h('span', 'race-list-days');
    days.appendChild(h('span', null, race.days_left === 0 ? '比賽日' : race.days_left + ' 天'));
    if (race.days_left !== 0) days.appendChild(h('span', 'race-weeks', raceWeeksText(race.days_left)));
    row.appendChild(days);
    list.appendChild(row);
  });
  return list;
}

/**
 * 下場賽事與目標賽事各一張卡片，各自記錄剩餘天數。
 * 下場賽事剛好就是目標賽事時合併成一張（同一個天數不必顯示兩次），
 * 標題並列兩個身分並加上目標標記。
 */
export function renderRaceCountdownBlock(races) {
  var block = h('div', 'today-block');
  var title = h('div', 'today-block-title race-title');
  title.appendChild(h('span', null, '賽事倒數'));
  block.appendChild(title);
  var grid = h('div', 'race-grid');
  var next = races && races.next;
  var target = races && races.target;
  if (next && next.is_target) {
    grid.classList.add('is-merged');
    grid.appendChild(raceCard('target', '下場賽事＝目標賽事', next, 'is-target'));
  } else {
    grid.appendChild(raceCard('flag', '下場賽事', next));
    grid.appendChild(raceCard('target', '目標賽事', target, 'is-target'));
  }
  block.appendChild(grid);

  // 卡片已顯示的場次（合併時 1 場、否則最多 2 場）以外還有別場，才需要「全部賽事」
  var all = (races && races.all) || [];
  var shownCount = grid.classList.contains('is-merged') ? 1 : (next ? 1 : 0) + (target ? 1 : 0);
  if (all.length > shownCount) {
    var panel = raceListPanel(all);
    panel.hidden = true;
    var toggle = h('button', 'race-all-btn', '全部賽事（' + all.length + '）');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      toggle.setAttribute('aria-expanded', String(!panel.hidden));
      toggle.classList.toggle('is-open', !panel.hidden);
    });
    title.appendChild(toggle);
    block.appendChild(panel);
  }
  return block;
}

/** 首頁頂端的資料新鮮度。 */
function renderFreshness(data) {
  var node = $('home-freshness');
  if (!node) return;
  clear(node);
  if (!data.freshness) return;
  var f = data.freshness;
  var days = f.days_behind;
  // days_behind 是拿 wellness_latest 與 activity_latest 兩者中較新的
  // 日期去算的（見後端 get_today() 的 most_recent），故顯示文字必須
  // 分開列出兩者，不能只挑其中一個日期配這個天數——挑錯就會出現
  // 「顯示活動日 09-04 卻標『今天』」這種自相矛盾的畫面（曾經發生過）。
  var parts = [];
  if (f.activity_latest) parts.push('活動 ' + displayDate(f.activity_latest));
  if (f.wellness_latest) parts.push('身體數據 ' + displayDate(f.wellness_latest));
  node.textContent = parts.length > 0
    ? '資料最後同步：' + parts.join('，')
    + (days === null || days === undefined ? '' : (days > 0 ? '（' + days + ' 天前）' : '（今天）'))
    : '尚無同步資料';
}

export function renderToday() {
  var node = $('today-body');
  clear(node);
  var data = state.today;
  if (!data) { showLoading(node, '載入今日狀態'); return; }

  renderFreshness(data);
  renderSidebarSync();

  var planBlock = h('div', 'today-block');
  var planned = data.planned || {};
  planBlock.appendChild(planBlockTitle(planned));
  planBlock.appendChild(renderTodayPlanRow('今日', planned.today));
  planBlock.appendChild(renderTodayPlanRow('明日', planned.tomorrow));

  // 課表在左、窄窄的「今日體感狀況」在右（區塊太窄時上下排）
  var top = h('div', 'today-top');
  top.appendChild(planBlock);
  // 今日體感要寫回伺服器，離線快照不顯示
  if (!isOfflineSnapshot()) top.appendChild(renderFeelBlock((data && data.date) || todayISO()));
  node.appendChild(top);

  // 綜合整備狀態與本週訓練狀況並排（區塊太窄時上下排）
  var pair = h('div', 'today-pair');
  pair.appendChild(renderTodayReadinessBlock(data.readiness));
  var weekTraining = h('div', 'today-block fig-block');
  weekTraining.id = 'week-training';
  pair.appendChild(weekTraining);
  node.appendChild(pair);
  renderWeekTraining();

  // 賽事倒數放在綜覽最下面
  node.appendChild(renderRaceCountdownBlock(data.races));
}

// ------------------------------------------------------------ 訓練負荷卡片

export var LOAD_TIPS = {
  tsb: [
    'TSB（訓練壓力平衡）＝ CTL − ATL：長期體能基礎減去近期疲勞。負值表示近期負荷高於長期基礎。',
    '負荷採 Banister TRIMP：分鐘 × 心率儲備百分比（%HRR）× 隨強度指數上升的權重，'
    + '一小時輕鬆跑約 85、一小時閾值跑約 165。另外標出 TSB 佔 CTL 的百分比，讓自訂尺度的數字可讀。',
    '參考值（Joe Friel，TrainingPeaks〈Applying the Numbers Part 3〉，原為 TSS 尺度）：'
    + '比賽日 +15～+25 通常表現最好；−10～+10 是過渡區；負值代表處於疲勞。'
    + 'TRIMP 與 TSS 定義不同（同一小時閾值跑約為 TSS 的 1.6 倍），絕對值不能直接套用；'
    + 'intervals.icu 改套在「TSB 佔 CTL 的百分比」，社群常引用 −30% 以下為高風險區——這是社群慣例，不是研究定論。本頁不據此判讀。'
  ],
  atl: [
    'ATL（急性訓練負荷）：每日訓練負荷（TRIMP）的 7 日指數加權平均，代表近期疲勞。',
    '指數加權：越近的日子權重越高，一週前的訓練影響約剩三分之一。'
  ],
  ctl: [
    'CTL（慢性訓練負荷）：每日訓練負荷（TRIMP）的 42 日指數加權平均，代表長期累積的體能基礎。',
    '變化很慢：穩定訓練數週才會明顯上升，停練也要數週才明顯下降。'
  ]
};

/**
 * 信賴度說明（併進 ⓘ 提示）。卡片上不再放「低信心度」標籤，太突兀；
 * 可信度仍照三欄位契約讀出，只是改成點 ⓘ 才看得到。信賴度為 high 時沒有額外說明。
 */
function confidenceTip(fact) {
  var c = fact && fact.confidence;
  if (!c || c.level === 'high') return [];
  var biases = (c.biases || []).map(function (b) { return b.detail; }).filter(Boolean);
  return ['信賴度：' + (CONFIDENCE_LABEL[c.level] || c.level)
    + (biases.length ? '。已知偏誤：' + biases.join('；') : '')];
}

// 訓練負荷區接在 TSB／ATL／CTL 後面的 Garmin 卡片（views/garmin.js）
var HOME_LOAD_GARMIN_KEYS = ['training_load', 'training_status', 'load_focus'];

function lastWith(rows, key) {
  for (var i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i][key] && typeof rows[i][key].value === 'number') return rows[i];
  }
  return null;
}

export function renderHomeLoad() {
  var node = $('home-load');
  if (!node) return;
  clear(node);
  var load = state.today && state.today.load;
  var kpi = state.todayKpi;
  if (!state.today || !kpi) { showLoading(node, '載入訓練負荷'); return; }
  if (!load) { showUnavailable(node, '今日尚無訓練負荷資料'); return; }
  if (load.as_of) node.appendChild(h('div', 'sync-fallback-note is-muted', unsyncedNote(load.as_of)));

  var rows = kpi.loadSeries || [];
  var cards = h('div', 'home-cards home-load-cards');
  var tsb = load.tsb && load.tsb.value;
  var ctl = load.ctl && load.ctl.value;
  cards.appendChild(kpiCard({
    label: '狀態平衡',
    abbr: 'TSB',
    value: typeof tsb === 'number' ? (tsb > 0 ? '+' : '') + C.formatNumber(tsb, 1) : '—',
    note: ctl ? '佔 CTL ' + (tsb > 0 ? '+' : '−') + Math.abs(tsb / ctl * 100).toFixed(0) + '%' : null,
    series: rows.map(function (r) { return r.tsb ? r.tsb.value : null; }),
    tip: LOAD_TIPS.tsb.concat(confidenceTip(load.tsb))
  }));
  cards.appendChild(kpiCard({
    label: '近期疲勞',
    abbr: 'ATL',
    value: load.atl ? C.formatNumber(load.atl.value, 1) : '—',
    note: '7 日指數加權',
    series: rows.map(function (r) { return r.atl ? r.atl.value : null; }),
    tip: LOAD_TIPS.atl.concat(confidenceTip(load.atl))
  }));
  cards.appendChild(kpiCard({
    label: '長期體能',
    abbr: 'CTL',
    value: load.ctl ? C.formatNumber(load.ctl.value, 1) : '—',
    note: '42 日指數加權',
    series: rows.map(function (r) { return r.ctl ? r.ctl.value : null; }),
    tip: LOAD_TIPS.ctl.concat(confidenceTip(load.ctl))
  }));

  // Garmin 的訓練負荷、訓練狀態、負荷焦點（2026-10-22 Fu 指定）：與趨勢頁「Garmin 數據」
  // 同一組卡片。排成兩列：上列本專案 TSB／ATL／CTL，下列這三張 Garmin。
  var garmin = (kpi.garminCards && kpi.garminCards.cards) || {};
  HOME_LOAD_GARMIN_KEYS.forEach(function (key) {
    if (!garmin[key]) return;
    var slot = h('div');
    renderGarminCard(key, garmin[key], slot);
    cards.appendChild(slot);
  });
  node.appendChild(cards);
}

// ------------------------------------------------------------ 體能狀況卡片

// 首頁體能狀況要放的 Garmin 卡片（2026-10-22 Fu 指定）：原有四張＋身體能量指數＋健康狀態
// （健康狀態取代原本的最大攝氧量，VO2max 仍在趨勢頁）。
// 卡片本身與趨勢頁「Garmin 數據」共用 views/garmin.js，這裡只決定放哪幾張。
var HOME_GARMIN_KEYS = ['hrv', 'heart_rate', 'sleep', 'training_readiness', 'body_battery', 'health_status'];

export function renderHomeBody() {
  var node = $('kpi-strip');
  if (!node) return;
  renderGarminWall(node, state.todayKpi && state.todayKpi.garminCards, HOME_GARMIN_KEYS, '載入體能狀況');
}

/**
 * 首頁的身體狀況與訓練負荷趨勢都取固定的近 30 天（不吃頂列 range），
 * 散在兩支既有端點，這裡併發取回。任一支失敗就當那幾張卡沒資料。
 */
export function loadTodayKpi() {
  Promise.all([
    api('/api/training-load?range=30d').catch(function () { return null; }),
    api('/api/garmin-cards?range=30d').catch(function () { return { cards: {} }; })
  ]).then(function (res) {
    var load = res[0];
    state.todayKpi = {
      loadSeries: (load && load.series) || [],
      garminCards: res[1]
    };
    renderHomeLoad();
    renderHomeBody();
  });
}

// ------------------------------------------------------------ 本課表週

export function renderWeekPlanList() {
  var node = $('week-plan-list');
  if (!node) return;
  clear(node);
  if (!state.weekDays) return;
  var title = $('h-week');
  if (title) title.textContent = '當週課表　' + planWeekLabel(state.weekDays);
  var block = renderPlanWeek(state.weekDays, { selectedDate: todayISO() });
  var zone2 = zone2Tile();
  var tiles = block.querySelector('.pw-tiles');
  if (zone2 && tiles) tiles.appendChild(zone2);
  node.appendChild(block);
}

/**
 * 摘要追加「Zone 2」圖卡：本週（週一到週日）跑步在 Z2 的時間與佔比，來自 /api/overview
 * 的 this_week（與課表週同一個週一起算的範圍）。區間資料還沒回來或週不一致時不顯示。
 * 比例條拆成低強度（Z1–Z2）／中強度（Z3）／高強度（Z4–Z5）三段，並在下方用文字與色點
 * 寫明每段的佔比；黑線標在 80% 處（80/20 的參考位置，不是判讀）。
 */
function zone2Tile() {
  var ov = state.homeOverview;
  var w = ov && ov.available && ov.this_week;
  if (!w || !state.weekDays || !state.weekDays.length || w.week_start !== state.weekDays[0].date) return null;
  var secs = {};
  w.zone_seconds.forEach(function (z) { secs[z.zone] = z.seconds; });
  var total = w.zone_seconds.reduce(function (sum, z) { return sum + z.seconds; }, 0);
  if (total <= 0) return null;
  function pctOf(zones) {
    return zones.reduce(function (sum, z) { return sum + (secs[z] || 0); }, 0) / total * 100;
  }

  var tile = planWeekTile('Zone 2 輕鬆跑');
  tile.title = '本週跑步在 Z2 的停留時間，百分比的分母是本週 Z1～Z5 的總時間';
  tile.appendChild(planWeekTileValue(C.formatDuration(secs[2] || 0),
    '佔本週跑步 ' + C.formatNumber(pctOf([2]), 0) + '%'));
  tile.appendChild(zoneSplitBar(w.zone_seconds.map(function (z) {
    return { zone: z.zone, pct: z.seconds / total * 100 };
  }), true));
  // 圖例：每段一列，左邊色點＋名稱，右邊該段的時間與佔比
  var legend = h('div', 'pw-legend');
  [
    { cls: 'bar-intensity-low', text: '低強度 Z1–Z2', zones: [1, 2] },
    { cls: 'bar-intensity-mid', text: '中強度 Z3', zones: [3] },
    { cls: 'bar-intensity-high', text: '高強度 Z4–Z5', zones: [4, 5] }
  ].forEach(function (item) {
    var secsOf = item.zones.reduce(function (sum, z) { return sum + (secs[z] || 0); }, 0);
    var entry = h('div', 'pw-legend-row');
    var name = h('span', 'pw-legend-item');
    name.appendChild(h('span', 'pw-legend-dot ' + item.cls));
    name.appendChild(document.createTextNode(item.text));
    entry.appendChild(name);
    entry.appendChild(h('span', 'pw-legend-val',
      C.formatDuration(secsOf) + '　' + C.formatNumber(pctOf(item.zones), 0) + '%'));
    legend.appendChild(entry);
  });
  legend.appendChild(h('div', 'pw-legend-note', '黑線 = 80% 參考位置'));
  tile.appendChild(legend);
  return tile;
}

export function renderWeekStrip() {
  var node = $('week-strip');
  clear(node);
  if (!state.weekDays) { showLoading(node, '載入本週'); return; }

  var grid = h('div', 'week-strip');
  state.weekDays.forEach(function (day) {
    var cell = h('div', 'week-cell day-' + day.day_state);
    var runnable = firstRunningActivity(day.activities);
    if (runnable) {
      cell.classList.add('is-clickable');
      cell.addEventListener('click', function () {
        navigate('sessions', runnable.id);
      });
    }
    cell.appendChild(h('span', 'week-cell-dow', dowLabel(day.date)));
    cell.appendChild(h('span', 'week-cell-date', day.date.slice(8, 10)));
    if (day.planned.length > 0) {
      var wt = day.planned[0].workout_type;
      cell.appendChild(h('span', 'week-cell-workout', WORKOUT_TYPE_LABEL[wt] || wt));
    }
    // 當週課表只看跑步；健走等其他活動不在這裡標點。暖身已併入主課，不另標一點
    var runs = day.activities.filter(function (act) { return act.is_running && !act.warmup_for_activity_id; });
    if (runs.length > 0) cell.appendChild(activityDots(runs));
    cell.title = displayDate(day.date) + '　' + (DAY_STATE_LABEL[day.day_state] || day.day_state);
    grid.appendChild(cell);
  });
  node.appendChild(grid);
}

/** 本課表週（週一到週日，見 plan-week.js）：週條、執行表與本週訓練卡共用同一份資料。 */
export function loadWeekStrip() {
  loadPlanWeek(todayISO()).then(function (days) {
    state.weekDays = days;
    renderWeekStrip();
    renderWeekPlanList();
    renderWeekTraining();
  }).catch(function (err) {
    showError($('week-strip'), err.message);
    if ($('week-training')) showError($('week-training'), err.message);
  });
}

// ------------------------------------------------------------ 本週訓練

// 環形進度圖的半徑（viewBox 單位）；圓周用來換算 stroke-dasharray
var RING_R = 26;
var RING_CIRCUMFERENCE = 2 * Math.PI * RING_R;

/** 完成率分級（%）→ 環的顏色：≥90 藍、≥50 綠、≥20 黃、其餘（<20）紅。 */
var PROGRESS_TIERS = [
  { min: 90, cls: 'pct-blue' },
  { min: 50, cls: 'pct-green' },
  { min: 20, cls: 'pct-yellow' },
  { min: -Infinity, cls: 'pct-red' }
];

function progressTierClass(pct) {
  for (var i = 0; i < PROGRESS_TIERS.length; i += 1) {
    if (pct >= PROGRESS_TIERS[i].min) return PROGRESS_TIERS[i].cls;
  }
  return 'pct-red';
}

/** 完成率環形圖：中間顯示百分比、環依完成率分級上色。超過 100% 時環畫滿，數字照實顯示。 */
function progressRing(pct) {
  var wrap = h('div', 'wt-ring ' + progressTierClass(pct));
  var filled = Math.max(0, Math.min(100, pct)) / 100 * RING_CIRCUMFERENCE;
  wrap.innerHTML = '<svg viewBox="3 3 58 58" aria-hidden="true">'
    + '<circle class="wt-ring-track" cx="32" cy="32" r="' + RING_R + '"/>'
    + '<circle class="wt-ring-fill" cx="32" cy="32" r="' + RING_R + '" '
    + 'stroke-dasharray="' + filled.toFixed(1) + ' ' + RING_CIRCUMFERENCE.toFixed(1) + '" '
    + 'transform="rotate(-90 32 32)"/></svg>';
  wrap.appendChild(h('span', 'wt-ring-pct', Math.round(pct) + '%'));
  return wrap;
}

/** 一列「實際 / 課表」：大數字＋淡化的分母；planned 為 null 時只顯示實際與單位。 */
function weekFigRow(label, done, planned, unit) {
  var row = h('div', 'wt-row');
  row.appendChild(h('span', 'wt-row-label', label));
  var val = h('span', 'wt-row-value');
  val.appendChild(h('span', 'wt-done', done));
  val.appendChild(h('span', 'wt-planned',
    planned === null ? unit : '/ ' + planned + (unit ? ' ' + unit : '')));
  row.appendChild(val);
  return row;
}

/** 秒 → 小時（小數一位），本週訓練狀況的時間一律用小時呈現。 */
function hoursText(sec) {
  return C.formatNumber(sec / 3600, 1);
}

/**
 * 本週訓練狀況：本課表週（週一到週日）實際跑量對照課表、實際累計時間，環形圖顯示里程完成率。
 * 時間只列實際，不對照預計時間（分母不可靠，見 plan-week.js planWeekSummary）。
 *
 * 放在「今天」區塊內、綜合整備狀態旁（#week-training 由 renderToday 建立）。
 * 課表週與今日資料各自載入、先後不定，兩邊資料回來時都會呼叫本函式；
 * 容器還不存在（今日資料未到）時直接略過，等 renderToday 建好再畫。
 */
export function renderWeekTraining() {
  var node = $('week-training');
  if (!node) return;
  clear(node);
  var title = h('div', 'today-block-title');
  title.appendChild(h('span', null, '本週訓練狀況'));
  if (state.weekDays && state.weekDays.length) {
    var first = state.weekDays[0].date;
    var last = state.weekDays[state.weekDays.length - 1].date;
    title.appendChild(h('span', 'today-note fig-title-date',
      C.shortDate(first) + ' (' + dowLabel(first) + ') ~ ' + C.shortDate(last) + ' (' + dowLabel(last) + ')'));
  }
  node.appendChild(title);

  var body = h('div');
  node.appendChild(body);
  if (!state.weekDays) { showLoading(body, '載入本週訓練狀況'); return; }

  var s = planWeekSummary(state.weekDays);
  if (s.plannedSessions === 0) {
    showUnavailable(body, '本週沒有課表，無法計算完成量');
    return;
  }

  // 里程與時間兩列在左、完成率環形圖在右；說明文字放在下方整列
  var card = h('div', 'wt-card');
  var figs = h('div', 'wt-figs');
  figs.appendChild(weekFigRow('距離',
    C.formatNumber(s.doneKm, 1),
    s.plannedKm > 0 ? C.formatNumber(s.plannedKm, 1) : '—', 'km'));
  figs.appendChild(weekFigRow('時間', hoursText(s.doneSec), null, 'hr'));
  figs.appendChild(weekFigRow('堂數', String(s.doneSessions), String(s.plannedSessions), '堂'));
  card.appendChild(figs);

  if (s.plannedKm > 0) {
    var pct = s.doneKm / s.plannedKm * 100;
    var ring = progressRing(pct);
    ring.title = '以距離計算：' + Math.round(pct) + '%';
    // 與綜合整備狀態的判定圖示同結構（圖＋下方一行字），兩張卡片置中後兩個圖的中心才會在同一高度
    var ringBox = h('span', 'wt-ring-box');
    ringBox.appendChild(ring);
    ringBox.appendChild(h('span', 'rd-status-label wt-ring-label', '完成率'));
    card.appendChild(ringBox);
  }
  body.appendChild(card);
}

// ------------------------------------------------------------ 戰力綜覽

function vdotCard(vdot) {
  var race = vdot.race || {};
  var ref = vdot.garmin_reference;
  var refText = null;
  if (ref && ref.predictions.length > 0) {
    var vals = ref.predictions.map(function (p) { return p.vdot; });
    var lo = Math.min.apply(null, vals);
    var hi = Math.max.apply(null, vals);
    refText = 'Garmin 對照 ' + C.formatNumber(lo, 1) + (hi !== lo ? '–' + C.formatNumber(hi, 1) : '');
  }
  var tip = [
    'Jack Daniels 的跑力指數：用一場全力成績反推的有氧能力分數。',
    '只採用標記為比賽或全力測驗的成績（3K／5K／10K 需在 90 天內，半馬／全馬 180 天內），'
    + '把實際距離與完賽時間代入 Daniels & Gilbert 公式：VDOT ＝ 該配速的攝氧成本 ÷ 能持續該時間的最大攝氧比例。'
    + '有手動計時成績的場次用手動計時成績。',
    '走勢線是歷年每場比賽各自算出的 VDOT（不受新鮮度門檻限制）。',
    '不採用一般訓練經心率換算的推估：那是次最大強度推估，對訓練量少的跑者會偏高。'
  ];
  if (ref) {
    tip.push('Garmin 對照值：把手錶 ' + ref.as_of + ' 的完賽預測（'
      + ref.predictions.map(function (p) {
        return p.distance + ' ' + C.formatDuration(p.time_sec) + ' → ' + C.formatNumber(p.vdot, 1);
      }).join('、')
      + '）用同一公式換算。Garmin 演算法未公開，僅供對照，不作為依據。');
  }
  var history = (vdot.history || []).map(function (x) { return x.vdot; });
  if (!race.value) {
    return kpiCard({
      label: '跑力', abbr: 'VDOT', value: '未評',
      badge: h('span', 'conf-badge conf-none', '無資料'),
      note: race.reason + (refText ? '；' + refText : ''),
      series: history.length > 1 ? history : null,
      tip: tip
    });
  }
  return kpiCard({
    label: '跑力',
    abbr: 'VDOT',
    value: C.formatNumber(race.value.value, 1),
    badge: confBadge(race.value),
    // 兩行：依據的成績、Garmin 對照（換行字元由 .kpi-note 的 pre-line 處理）
    note: '依 ' + C.shortDate(race.basis.date) + ' ' + race.basis.distance + (refText ? '\n' + refText : ''),
    series: history.length > 1 ? history : null,
    tip: tip
  });
}

// 展開中的距離（distance 代碼）；重繪時保留，切換頁面再回來也還在
var pbOpenDistance = null;

/** 卡片上的賽事名稱：個人測驗一律顯示「個人測驗」，完整名稱留給展開清單 */
function pbCardName(r) {
  return r.personal_test ? '個人測驗' : (r.name || '');
}

function pbCard(pb) {
  var count = pb.records.length;
  var expandable = count > 0;
  var card = h(expandable ? 'button' : 'div', 'pb-card');
  if (count === 0) card.className += ' is-empty';
  if (expandable) {
    card.type = 'button';
    card.className += ' is-expandable' + (pbOpenDistance === pb.distance ? ' is-open' : '');
    card.setAttribute('aria-expanded', pbOpenDistance === pb.distance ? 'true' : 'false');
    card.setAttribute('aria-controls', 'pb-detail');
    card.addEventListener('click', function () {
      pbOpenDistance = pbOpenDistance === pb.distance ? null : pb.distance;
      renderAbility();
      var target = pbOpenDistance ? $('pb-detail') : null;
      if (target) target.focus({ preventScroll: true });
    });
  }

  var top = h('div', 'pb-card-head');
  top.appendChild(h('span', 'pb-distance', pb.label));
  if (expandable) top.appendChild(h('span', 'pb-count', count > 1 ? '共 ' + count + ' 場' : ''));
  card.appendChild(top);

  if (count === 0) {
    card.appendChild(h('div', 'pb-empty-text', '尚無紀錄'));
    return card;
  }
  var best = pb.records[0];
  card.appendChild(h('div', 'pb-time', C.formatDuration(best.time_sec)));
  card.appendChild(h('div', 'pb-pace', C.formatPace(best.pace_sec_per_km) + '/km'));
  card.appendChild(h('div', 'pb-date', displayDate(best.date)));
  card.appendChild(h('div', 'pb-vdot', 'VDOT ' + C.formatNumber(best.vdot, 1)));
  var name = h('div', 'pb-name', pbCardName(best));
  name.title = best.name || '';
  card.appendChild(name);
  return card;
}

function pbDetail(pb) {
  var panel = h('div', 'pb-detail');
  panel.id = 'pb-detail';
  panel.tabIndex = -1;
  var head = h('div', 'pb-detail-head');
  head.appendChild(h('h3', 'pb-detail-title', pb.label + ' 歷史成績（共 ' + pb.records.length + ' 場'
    + (pb.records.length > 1 ? '，由快到慢）' : '）')));
  var close = h('button', 'pb-detail-close', '✕');
  close.type = 'button';
  close.setAttribute('aria-label', '收合 ' + pb.label + ' 歷史成績');
  close.addEventListener('click', function () {
    pbOpenDistance = null;
    renderAbility();
  });
  head.appendChild(close);
  panel.appendChild(head);

  var list = h('ol', 'pb-list');
  pb.records.forEach(function (r, i) {
    var item = h('li');
    var row = h('button', 'pb-row' + (i === 0 ? ' is-best' : ''));
    row.type = 'button';
    row.setAttribute('aria-label', '第 ' + (i + 1) + ' 名 ' + C.formatDuration(r.time_sec) + '，' + r.date + '，' + (r.name || '') + '，檢視單場分析');
    row.appendChild(h('span', 'pb-row-rank', String(i + 1)));
    var time = h('span', 'pb-row-time', C.formatDuration(r.time_sec));
    if (r.official) {
      var manual = h('span', 'pb-manual', '手動');
      manual.title = '時間為人工填入（大會成績或現場計時），非手錶紀錄';
      time.appendChild(manual);
    }
    row.appendChild(time);
    row.appendChild(h('span', 'pb-row-pace', C.formatPace(r.pace_sec_per_km) + '/km'));
    row.appendChild(h('span', 'pb-row-date', displayDate(r.date)));
    row.appendChild(h('span', 'pb-row-name', r.name || ''));
    row.appendChild(h('span', 'pb-row-vdot', 'VDOT ' + C.formatNumber(r.vdot, 1)));
    row.addEventListener('click', function () { navigate('sessions', r.id); });
    item.appendChild(row);
    list.appendChild(item);
  });
  panel.appendChild(list);
  return panel;
}

/** VDOT 卡＋各距離個人最佳成績卡（點卡片展開該距離的全部成績）。 */
var ABILITY_PB_ORDER = ['half_marathon', 'marathon', '3k', '5k', '10k'];

function abilityPbRank(distance) {
  var i = ABILITY_PB_ORDER.indexOf(distance);
  return i === -1 ? ABILITY_PB_ORDER.length : i;
}

export function renderAbility() {
  var node = $('home-ability');
  if (!node) return;
  closeInfoPopover(false); // 重繪會換掉 ⓘ 按鈕，開著的說明要先關
  clear(node);
  var ov = state.homeOverview;
  if (!ov) { showLoading(node, '載入戰力綜覽'); return; }
  if (!ov.available) { showUnavailable(node, ov.reason); return; }

  var byDistance = {};
  ov.personal_bests.forEach(function (pb) { byDistance[pb.distance] = pb; });
  if (pbOpenDistance && !(byDistance[pbOpenDistance] && byDistance[pbOpenDistance].records.length)) {
    pbOpenDistance = null;
  }

  // 顯示順序：跑力、半馬、全馬；3K、5K、10K（一列三張，長距離在前）。
  // API 回傳的是由短到長，這只是呈現順序；沒列在 ABILITY_PB_ORDER 的距離排在最後
  var grid = h('div', 'ability-grid');
  grid.appendChild(vdotCard(ov.vdot));
  ov.personal_bests.slice().sort(function (a, b) {
    return abilityPbRank(a.distance) - abilityPbRank(b.distance);
  }).forEach(function (pb) { grid.appendChild(pbCard(pb)); });
  node.appendChild(grid);
  node.appendChild(h('p', 'chart-note',
    '個人最佳成績為生涯紀錄：標記為 Race 的場次（含個人計時測驗），每個距離顯示最佳一筆，點卡片看全部成績。'
    + '賽事名稱讀自學員檔 athlete_profile.md，配速一律為「時間 ÷ 標準距離」。'));
  if (pbOpenDistance) node.appendChild(pbDetail(byDistance[pbOpenDistance]));
}

/**
 * 首頁只用 /api/overview 裡不受時間範圍影響的欄位（VDOT、個人最佳成績、本週 Zone 2），
 * 所以固定取一次、與頂列 range 無關（趨勢頁的區間摘要另外用 state.overview）。
 */
export function loadHomeOverview() {
  api('/api/overview?range=30d').then(function (data) {
    state.homeOverview = data;
    renderAbility();
    renderWeekPlanList();
  }).catch(function (err) {
    showError($('home-ability'), err.message);
  });
}

// ------------------------------------------------------------ 最近訓練

export function renderRecentSessions() {
  var node = $('recent-sessions');
  if (!node) return;
  clear(node);
  if (!state.recentActivities) { showLoading(node, '載入近期活動'); return; }
  if (state.recentActivities.length === 0) {
    node.appendChild(h('div', 'state-msg state-unavailable', '近 30 天沒有活動紀錄'));
    return;
  }

  var list = h('div', 'recent-list');
  var head = h('div', 'recent-row recent-head');
  ['日期', '活動', '距離', '時間', '配速', '心率'].forEach(function (t) {
    var cell = h('span', null, t);
    // 窄版數據收成兩行（距離 時間／配速 · 心率），表頭只剩一格，補上「／時間」
    if (t === '距離') cell.appendChild(h('span', 'rc-head-extra', '／時間'));
    head.appendChild(cell);
  });
  list.appendChild(head);

  // 暖身是另存的獨立活動：不單獨成列，折進主課（主課的「活動」欄標「含暖身 x km」）
  var items = state.recentActivities.filter(function (item) { return !item.activity.warmup_for_activity_id; });

  items.slice(0, 8).forEach(function (item) {
    var a = item.activity;
    // 跑步才有單場分析可看（同行事曆）；健走、重訓等整列淡色、不可點
    var row = h(a.is_running ? 'button' : 'div', 'recent-row' + (a.is_running ? '' : ' is-other'));
    if (a.is_running) {
      row.type = 'button';
      row.title = '看單場分析';
      row.addEventListener('click', function () { navigate('sessions', a.id); });
    }

    row.appendChild(h('span', 'rc-date', C.shortDate(item.date) + ' (' + dowLabel(item.date) + ')'));

    var act = h('span', 'rc-activity');
    act.appendChild(h('span', 'rc-main', a.is_running
      ? (WORKOUT_TYPE_LABEL[a.workout_type] || a.workout_type || '未分類')
      : (ACTIVITY_TYPE_LABEL[a.activity_type] || a.activity_type)));
    // 活動欄第二行只放「含暖身 x km」；時間改放在數據欄
    if (a.is_running && item.warmup) act.appendChild(h('span', 'rc-sub', warmupLabel(item.warmup)));
    row.appendChild(act);

    // 數據：公里、時間（大字）｜配速、心率（灰色小字）。缺值一律顯示「—」，
    // 每個節點都存在，寬版各自成欄、缺值也不會讓後面的欄位錯位；窄版收成兩行
    var actual = h('span', 'rc-actual');
    actual.appendChild(h('span', 'rc-dist', a.is_running && a.distance_km ? C.formatNumber(a.distance_km, 1) + ' km' : '—'));
    actual.appendChild(h('span', 'rc-time', a.duration_sec ? C.formatDuration(a.duration_sec) : '—'));
    var meta = h('span', 'rc-meta');
    var hasPace = a.is_running && a.distance_km && a.duration_sec;
    meta.appendChild(h('span', 'rc-pace', hasPace ? C.formatPace(a.duration_sec / a.distance_km) + '/km' : '—'));
    meta.appendChild(h('span', 'rc-sep', ' · '));
    meta.appendChild(h('span', 'rc-hr', a.avg_hr_bpm ? Math.round(a.avg_hr_bpm) + ' bpm' : '—'));
    actual.appendChild(meta);
    row.appendChild(actual);

    list.appendChild(row);
  });
  node.appendChild(list);
}

/**
 * 近 30 天的所有活動（含健走、重訓等），由新到舊。
 * 資料取自 /api/calendar；同一天的活動 API 依開始時間由早到晚，這裡反過來。
 */
export function loadRecentSessions() {
  var today = todayISO();
  api('/api/calendar?start=' + addDaysISO(today, -29) + '&end=' + today).then(function (data) {
    var items = [];
    ((data && data.days) || []).slice().reverse().forEach(function (d) {
      var warmups = warmupsByMain(d.activities);
      (d.activities || []).slice().reverse().forEach(function (a) {
        items.push({ date: d.date, activity: a, warmup: warmups[a.id] || null });
      });
    });
    state.recentActivities = items;
    renderRecentSessions();
  }).catch(function (err) {
    showError($('recent-sessions'), err.message);
  });
}

// ------------------------------------------------------------ 載入

export function loadToday() {
  showLoading($('today-body'), '載入今日狀態');
  api('/api/today').then(function (data) {
    state.today = data;
    renderToday();
    renderHomeLoad();
    renderHomeBody(); // HRV 的事實（含信賴度）與 Garmin 狀態來自 /api/today
    // 設定頁的「最後同步」與側邊列同源，資料回來時一併更新（設定頁此刻
    // 可能不在畫面上，但它沒有圖表、不吃容器寬度，直接寫入即可）
    renderSettingsSync();
  }).catch(function (err) {
    showError($('today-body'), err.message);
  });
}

/** 首頁全部區塊的資料（同步完成後也呼叫這裡重新整理）。 */
export function loadHome() {
  loadToday();
  loadTodayKpi();
  loadWeekStrip();
  loadHomeOverview();
  loadRecentSessions();
}
