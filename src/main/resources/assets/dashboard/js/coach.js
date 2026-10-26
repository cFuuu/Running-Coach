/**
 * coach.js — AI 教練聊天室面板（DASHBOARD_TASKS.md D.4／S3；TODO N-35／N-36）
 *
 * 【架構契約】
 *   本檔只負責聊天面板：對話管理、追蹤橫條、SSE 串流、訊息渲染、「分析近況」。
 *   透過 core/state.js 的 getViewContext() 讀 App 的檢視脈絡、只讀 athleteId，
 *   **不改 App 的 state**，也不 import charts.js。
 *
 * 【D.1 硬約束在本檔的含義】
 *   - 面板不在前端拼數據餵模型：送出的只有「使用者打的字」＋一行〔檢視脈絡〕
 *     （view／session_id／range 等識別資訊），數據一律由模型自己呼叫 MCP 工具取得，
 *     那些工具的回傳才帶信賴度（PLAN.md §7.3 三欄位契約）。
 *   - 工具呼叫顯示成可展開 chip：把行為約束 4「回述事實清單」變成介面上看得見的
 *     東西——使用者可以自己核對教練查了什麼、拿到什麼。
 *   - 寫入權限依 PLAN.md §10：對話框只能新增／結案教練自建的追蹤事項、記錄學員明說的
 *     體感（後端 PANEL_WRITE_TOOL_NAMES 把關）。這三個工具在介面上不顯示成 chip，
 *     而是顯示成看得懂的結果（虛線卡片、「✓ 已記錄」、一行小字），讓學員知道教練改了什麼。
 *
 * 【對 API 契約的假設（N-36 前後端平行開發，合併時以此對照後端）】
 *   - GET  /api/chat/status → {enabled, reason, model, client_allowed, backend}；
 *     能不能發問只看 enabled && client_allowed，不能時的說明直接用 reason。
 *   - GET  /api/chat/conversations → {conversations:[{id,title,created_at,updated_at}]}，
 *     updated_at 新到舊；啟動時開第一筆（最近更新的）。
 *   - POST /api/chat/conversations → {id,title,created_at,updated_at}。「＋ 新對話」只把面板
 *     切成空白草稿，第一次送出（或在草稿上按「分析近況」）時才真的建立。
 *   - GET  /api/chat/conversations/{id} → {..., messages:[{id,seq,role,kind,content,created_at}]}
 *     · content 是 Anthropic content blocks；tool_result 在下一則 user 訊息裡。
 *     · 學員訊息的 text 中，以「〔」開頭的行是介面或後端加的（〔檢視脈絡〕〔日期〕〔分析近況〕），
 *       顯示時去掉。
 *     · kind === 'analysis'，或學員訊息第一段 text 以〔分析近況〕開頭 → 分析回合。
 *     · created_at 可能帶時區（UTC）或不帶（本地時間），一律 new Date() 解析成本地日期。
 *     · 同一回合教練可能被拆成多則 assistant 訊息（中間夾 tool_result 的 user 訊息），
 *       前端把它們合併成一個教練氣泡。
 *   - DELETE /api/chat/conversations/{id} → 204。
 *   - POST /api/chat/conversations/{id}/messages {text} → SSE：
 *     text{delta}／tool_use{id,name,input}／tool_result{tool_use_id,name,is_error,content}／
 *     done{message_id,stop_reason}／error{message}；忙碌 409 {detail}。
 *     後端在收到請求時（送模型前）就建立標題，所以拿到回應標頭後立刻重抓清單。
 *   - GET  /api/chat/tracking → {items:[{id,note,start_date,status（或 main 實作的 tracking_status）:'proposed'|'tracking',
 *     last_followup_date,last_mentioned_date,next_followup_date,action,action_reason}]}
 *   - POST /api/chat/tracking/{id} {action:'accept'|'decline'|'resolve'|'stop'} → 更新後的事項。
 *   - GET  /api/chat/recent-advice → {advice:[{date,conversation_id,text}]}
 *   - 工具名稱：後端應已去掉 mcp__running-coach__ 前綴，前端仍保險再去一次。
 *   - propose_tracking_item：input {note, start_date}；tool_result.content 是 JSON 字串
 *     （main 的實作為 {saved, item:{id, note, ...}} 或 {saved:false, reason}），
 *     新事項 id 依序取 id／item_id／item.id，原話取 note／item.note／input.note。
 *   - update_tracking_item：input {item_id, action}
 *     （accept／decline／followed_up／mentioned／stale_asked／resolve／stop）；
 *     tool_result {saved, item} 或 {saved:false, reason}。
 *   - record_daily_feel：input {date, feel}；tool_result JSON {saved, reason?, date?, feel?}。
 *   - GET  /api/analysis/status → {enabled, reason, client_allowed, running}
 *   - POST /api/analysis?conversation_id= → SSE：start{id,conversation_id}／tool_use／tool_result／
 *     text／denied{tool}／done{report,is_error,...,conversation_id}／error{message,id}。
 *     報告由後端存成該對話的一回合，前端完成後重新載入對話，不自己拼報告。
 *   - 離線快照（isOfflineSnapshot）時以上 GET 由 window.MOCK_API.chatRequest 回應，
 *     非 GET 一律被拒；tracking／recent-advice 抓不到就安靜隱藏橫條。
 *
 * 【零建置、無 CDN】
 *   Markdown 是自寫的極簡子集（段落／清單／粗體／行內碼／標題），先 escape 再轉，
 *   不引任何函式庫。
 */

import { $, clear, h } from './core/dom.js';
import { getViewContext } from './core/state.js';
import { withAthlete, isOfflineSnapshot } from './core/api.js';
import { openCoachPanel } from './shell/coach-frame.js';

// ------------------------------------------------------------ 狀態

var state = {
  status: null,        // /api/chat/status 的回傳（null = 還沒問到）
  conversations: [],
  activeId: null,      // null = 還沒有對話，或按了「＋ 新對話」的空白草稿
  messages: [],        // 畫面上的訊息（見下方「訊息模型」）
  streaming: false,
  lastSentText: null,  // 供 error 後重試：該回合未落地，重試＝重送
  loadError: null,
  error: null,
  tracking: { items: [], advice: [], loaded: false, expanded: false, error: null },
  analysis: null       // 進行中的「分析近況」：{tools, denied, status, error}
};

/**
 * 訊息模型（畫面用，不等於後端存的 Anthropic content blocks）：
 *   {role: 'user',  text, createdAt, kind}          kind = 'chat' | 'analysis'
 *   {role: 'coach', text, tools, createdAt, kind}
 *     tools: [{id, name, input, result, isError}]，依 tool_use 建立、由 tool_result 補上結果。
 * thinking 區塊不顯示（D.4）。
 */

var MAX_TOOL_JSON_CHARS = 20000; // chip 展開時的顯示上限，避免超長 JSON 卡住畫面

// 離線快照的對話只帶工具名稱與參數（見 build_dashboard_offline.py），沒有查詢結果
var OFFLINE_TOOL_RESULT = '（離線檔不含查詢結果）';

var TOOL_PREFIX = 'mcp__running-coach__';

// 這三個寫入型工具不顯示成 chip，而是顯示成學員看得懂的結果（PLAN.md §10）
var TOOL_PROPOSE = 'propose_tracking_item';
var TOOL_UPDATE = 'update_tracking_item';
var TOOL_FEEL = 'record_daily_feel';

var FEEL_LABEL = { tired: '疲憊', normal: '普通', energetic: '充沛' };

var UPDATE_LABEL = {
  accept: '已開始追蹤',
  decline: '不追蹤',
  followed_up: '已追問',
  mentioned: '已記下你提到',
  stale_asked: '已問是否繼續追蹤',
  resolve: '已結案（痊癒）',
  stop: '已停止追蹤'
};

var WAITING_TEXT = '教練準備中…（約 10～20 秒）';
var BUSY_TEXT = '教練正在回覆或分析中，請稍候';
var PERMISSION_NOTE = '教練在這裡只能：新增／結案它建立的追蹤事項、記錄你說的體感。'
  + '課表、Garmin 數據、成績不能改。';

var WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

// ------------------------------------------------------------ 小工具

function isMock() {
  return !!window.MOCK_API;
}

/** 能不能發問：聊天啟用、來源允許。false 時面板只能瀏覽過去的對話。 */
function canChat() {
  return !!(state.status && state.status.enabled && state.status.client_allowed);
}

/** 正在回覆或分析：兩者共用後端同一把鎖，前端也一起鎖住送出。 */
function isBusy() {
  return state.streaming || !!(state.analysis && state.analysis.status === 'running');
}

/** 帶 HTTP 狀態碼的錯誤，供呼叫端分辨 409（忙碌）。 */
function httpError(message, status) {
  var err = new Error(message);
  err.status = status;
  return err;
}

/** 一般（非串流）請求。mock／離線模式改走 window.MOCK_API.chatRequest。 */
function api(path, options) {
  if (isMock() && window.MOCK_API.chatRequest) {
    return window.MOCK_API.chatRequest(path, options || {});
  }
  var opts = options || {};
  return fetch('.' + path, {
    method: opts.method || 'GET',
    headers: opts.body
      ? { 'Content-Type': 'application/json', Accept: 'application/json' }
      : { Accept: 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined
  }).then(function (res) {
    if (res.status === 204) return null;
    return res.json().catch(function () {
      throw httpError('伺服器回應不是 JSON（HTTP ' + res.status + '）', res.status);
    }).then(function (data) {
      if (!res.ok) {
        throw httpError((data && data.detail) || ('HTTP ' + res.status), res.status);
      }
      return data;
    });
  });
}

/** 串流請求失敗時把後端 detail 取出來（409 換成固定的中文說明）。 */
function streamError(res) {
  return res.json().catch(function () { return {}; }).then(function (data) {
    if (res.status === 409) throw httpError(BUSY_TEXT, 409);
    throw httpError((data && data.detail) || ('HTTP ' + res.status), res.status);
  });
}

function toolName(name) {
  var n = String(name || '');
  return n.indexOf(TOOL_PREFIX) === 0 ? n.slice(TOOL_PREFIX.length) : n;
}

function parseJson(text) {
  if (text === null || text === undefined) return null;
  if (typeof text === 'object') return text;
  try {
    return JSON.parse(String(text));
  } catch (e) {
    return null;
  }
}

/** 'YYYY-MM-DD…' → 'M/D'（日期字串直接拆，不經 Date 以免時區位移）。 */
function shortDate(iso) {
  var m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return '';
  return parseInt(m[2], 10) + '/' + parseInt(m[3], 10);
}

/** created_at（帶或不帶時區）→ 本地日期鍵 'YYYY-MM-DD'；解析不了回 null。 */
function localDayKey(createdAt) {
  if (!createdAt) return null;
  var d = new Date(createdAt);
  if (isNaN(d.getTime())) return null;
  var p2 = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate());
}

/** 日期分隔的字樣：「10/27（二）」。 */
function dayLabel(dayKey) {
  var parts = dayKey.split('-');
  var d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
  return (d.getMonth() + 1) + '/' + d.getDate() + '（' + WEEKDAYS[d.getDay()] + '）';
}

/** 學員訊息顯示用：去掉以「〔」開頭的行（〔檢視脈絡〕〔日期〕〔分析近況〕是介面／後端加的）。 */
function visibleUserText(text) {
  return String(text || '').split('\n').filter(function (line) {
    return line.replace(/^\s+/, '').charAt(0) !== '〔';
  }).join('\n').trim();
}

// ------------------------------------------------------------ 極簡 Markdown
//
// 刻意只支援教練回覆實際會用到的一小組語法。先 escape 再轉標記，
// 所以任何模型輸出都不可能變成可執行的 HTML。

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 行內語法：`code` 與 **bold**（escape 之後才套用，故不會產生非預期標籤）。 */
function inlineMarkdown(escaped) {
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

/**
 * 把極簡 Markdown 轉成 HTML 片段。支援：# 標題、- 清單、數字清單、段落。
 * @param {string} text
 * @returns {string} HTML
 */
function renderMarkdown(text) {
  var lines = String(text || '').split('\n');
  var out = [];
  var listType = null; // 'ul' | 'ol' | null
  var paragraph = [];

  function flushParagraph() {
    if (paragraph.length === 0) return;
    out.push('<p>' + inlineMarkdown(escapeHtml(paragraph.join('\n'))).replace(/\n/g, '<br>') + '</p>');
    paragraph = [];
  }
  function closeList() {
    if (listType) { out.push('</' + listType + '>'); listType = null; }
  }
  function openList(type) {
    if (listType !== type) { closeList(); out.push('<' + type + '>'); listType = type; }
  }

  lines.forEach(function (raw) {
    var line = raw.replace(/\s+$/, '');
    if (line === '') { flushParagraph(); closeList(); return; }

    var heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); closeList();
      var level = Math.min(heading[1].length + 2, 6); // # → h3（面板內不用 h1/h2）
      out.push('<h' + level + '>' + inlineMarkdown(escapeHtml(heading[2])) + '</h' + level + '>');
      return;
    }

    var bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      flushParagraph(); openList('ul');
      out.push('<li>' + inlineMarkdown(escapeHtml(bullet[1])) + '</li>');
      return;
    }

    var numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (numbered) {
      flushParagraph(); openList('ol');
      out.push('<li>' + inlineMarkdown(escapeHtml(numbered[1])) + '</li>');
      return;
    }

    closeList();
    paragraph.push(line);
  });

  flushParagraph();
  closeList();
  return out.join('');
}

// ------------------------------------------------------------ 檢視脈絡與建議問題

/**
 * 依目前看的 view 產生「問這一頁」建議問題（D.4 明列的六組）。
 * @returns {string[]}
 */
function suggestionsFor(ctx) {
  if (!ctx) return [];
  switch (ctx.view) {
    case 'home':
      return ['今天照課表練還是調整？', '最近一場練得如何？'];
    case 'calendar':
      return ['這週課表跟實際對得上嗎？'];
    case 'sessions':
      return ctx.sessionId
        ? ['解讀這場訓練的品質', '心率漂移的數值代表什麼？']
        : [];
    case 'trends':
      return ['目前負荷該增量、維持還是降量？', '近期身體數據有什麼要注意？'];
    default:
      return [];
  }
}

/**
 * 附在訊息末尾的〔檢視脈絡〕行——只給識別資訊（view／id／range），
 * **不貼任何數據**：模型要數字就自己呼叫工具，那樣才帶得到信賴度。
 * 日期由後端另加〔日期〕區塊（N-36 決策 6），這裡不重複附。
 * @returns {string} 空字串表示沒有可附的脈絡
 */
function contextLine(ctx) {
  if (!ctx) return '';
  var parts = ['view=' + ctx.view];
  if (ctx.sessionId) parts.push('session_id=' + ctx.sessionId);
  if (ctx.month) parts.push('month=' + ctx.month);
  if (ctx.range && ctx.view !== 'home' && ctx.view !== 'calendar') {
    parts.push('range=' + ctx.range);
  }
  if (ctx.date) parts.push('date=' + ctx.date);
  return '〔檢視脈絡〕' + parts.join(' ');
}

function viewContext() {
  return getViewContext();
}

// ------------------------------------------------------------ 版面

/** 建立面板骨架。只做一次。 */
function buildPanel() {
  var body = $('coach-body');
  if (!body) return false;
  clear(body);

  var wrap = h('div', 'coach-layout');

  // 對話管理列：下拉選單＋「＋ 新對話」＋刪除
  var bar = h('div', 'coach-convbar');
  var select = h('select', 'coach-conv-select');
  select.id = 'coach-conv-select';
  select.setAttribute('aria-label', '選擇對話');
  select.addEventListener('change', function () {
    var id = parseInt(select.value, 10);
    if (!isNaN(id)) openConversation(id);
  });
  var newBtn = h('button', 'coach-new-btn', '＋ 新對話');
  newBtn.type = 'button';
  newBtn.id = 'coach-new';
  newBtn.hidden = !canChat();
  newBtn.addEventListener('click', startDraftConversation);
  var delBtn = h('button', 'coach-icon-btn', '🗑');
  delBtn.type = 'button';
  delBtn.id = 'coach-delete';
  delBtn.title = '刪除這則對話';
  delBtn.setAttribute('aria-label', '刪除這則對話');
  delBtn.hidden = isOfflineSnapshot();
  delBtn.addEventListener('click', deleteActiveConversation);
  bar.appendChild(select);
  bar.appendChild(newBtn);
  bar.appendChild(delBtn);

  // 追蹤橫條（沒有追蹤事項也沒有近期建議時整個隱藏）
  var track = h('div', 'coach-track');
  track.id = 'coach-track';
  track.hidden = true;

  // 訊息區（自己捲動）
  var log = h('div', 'coach-log');
  log.id = 'coach-log';
  log.setAttribute('role', 'log');
  log.setAttribute('aria-live', 'polite');

  // 建議問題
  var chips = h('div', 'coach-chips');
  chips.id = 'coach-chips';

  // 輸入區（固定在面板底部）
  var form = h('form', 'coach-form');
  form.id = 'coach-form';
  var input = h('textarea', 'coach-input');
  input.id = 'coach-input';
  input.rows = 2;
  input.placeholder = '問教練…（Enter 送出，Shift+Enter 換行）';
  input.setAttribute('aria-label', '輸入問題');
  input.addEventListener('keydown', function (evt) {
    if (evt.key === 'Enter' && !evt.shiftKey && !evt.isComposing) {
      evt.preventDefault();
      form.dispatchEvent(new Event('submit', { cancelable: true }));
    }
  });
  var send = h('button', 'coach-send', '送出');
  send.type = 'submit';
  send.id = 'coach-send';
  form.appendChild(input);
  form.appendChild(send);
  form.addEventListener('submit', function (evt) {
    evt.preventDefault();
    submitMessage(input.value);
  });

  // 教練在對話框能改什麼（PLAN.md §10）
  var rule = h('p', 'coach-rule', PERMISSION_NOTE);
  rule.id = 'coach-rule';

  // 不能發問時（停用、離線快照）取代輸入區的說明
  var note = h('p', 'coach-readonly-note');
  note.id = 'coach-readonly-note';
  note.hidden = true;

  wrap.appendChild(bar);
  wrap.appendChild(track);
  wrap.appendChild(log);
  wrap.appendChild(chips);
  wrap.appendChild(form);
  wrap.appendChild(rule);
  wrap.appendChild(note);
  body.appendChild(wrap);
  return true;
}

/** 依「能不能發問」切換輸入區與說明。 */
function renderComposer() {
  var form = $('coach-form');
  var note = $('coach-readonly-note');
  var rule = $('coach-rule');
  if (!form || !note) return;
  var reason = null;
  if (!canChat()) {
    reason = '目前只能瀏覽過去的對話'
      + (state.status && state.status.reason ? '：' + state.status.reason : '') + '。';
  }
  form.hidden = reason !== null;
  if (rule) rule.hidden = reason !== null;
  note.hidden = reason === null;
  note.textContent = reason || '';
  var newBtn = $('coach-new');
  if (newBtn) newBtn.hidden = !canChat();
  renderBusy();
}

/** 面板不可用時（非本機）的說明，並鎖住輸入。 */
function renderDisabled(message) {
  var body = $('coach-body');
  if (!body) return;
  clear(body);
  var box = h('div', 'coach-disabled');
  box.appendChild(h('p', 'coach-disabled-msg', message));
  body.appendChild(box);
}

function renderConversationSelect() {
  var select = $('coach-conv-select');
  if (!select) return;
  clear(select);
  if (state.activeId === null) {
    // 可發問時是空白草稿：第一次送出時才建立對話（N-36 決策 5）
    var draft = h('option', null, canChat() ? '（新對話）'
      : (state.conversations.length > 0 ? '（選擇對話）' : '（尚無對話）'));
    draft.value = '';
    draft.selected = true;
    select.appendChild(draft);
  }
  state.conversations.forEach(function (c) {
    var opt = h('option', null, c.title || '（未命名對話）');
    opt.value = String(c.id);
    if (c.id === state.activeId) opt.selected = true;
    select.appendChild(opt);
  });
  select.disabled = select.options.length <= 1 || isBusy();
  var del = $('coach-delete');
  if (del) del.disabled = state.activeId === null || isBusy();
  renderComposer();
}

function renderChips() {
  var node = $('coach-chips');
  if (!node) return;
  clear(node);
  if (isBusy()) return;
  if (!canChat()) return;
  suggestionsFor(viewContext()).forEach(function (text) {
    var chip = h('button', 'coach-chip', text);
    chip.type = 'button';
    chip.addEventListener('click', function () { submitMessage(text); });
    node.appendChild(chip);
  });
}

/** 回覆或分析中：鎖住送出、下拉、刪除與「分析近況」。 */
function renderBusy() {
  var busy = isBusy();
  var send = $('coach-send');
  var input = $('coach-input');
  if (send) {
    send.disabled = busy;
    send.textContent = state.streaming ? '回覆中…' : (busy ? '分析中…' : '送出');
  }
  if (input) input.disabled = busy;
  var select = $('coach-conv-select');
  if (select) select.disabled = select.options.length <= 1 || busy;
  var del = $('coach-delete');
  if (del) del.disabled = state.activeId === null || busy;
  var newBtn = $('coach-new');
  if (newBtn) newBtn.disabled = busy;
  var analyze = $('analyze-btn');
  if (analyze) analyze.disabled = busy;
  renderChips();
}

// ------------------------------------------------------------ 追蹤橫條（N-35 ④）

function loadTracking() {
  // 兩個端點各自容錯：任一抓不到就當沒有（離線快照或舊後端），不顯示錯誤
  var items = api(withAthlete('/api/chat/tracking')).then(function (data) {
    return ((data && data.items) || []).map(normalizeItem);
  }, function () { return null; });
  var advice = api(withAthlete('/api/chat/recent-advice')).then(function (data) {
    return (data && data.advice) || [];
  }, function () { return []; });
  return Promise.all([items, advice]).then(function (res) {
    state.tracking.loaded = res[0] !== null;
    state.tracking.items = res[0] || [];
    state.tracking.advice = res[1];
    renderTracking();
    renderLog(); // 對話裡的「追蹤／不用」按鈕依事項目前狀態決定顯示與否
  });
}

/** 後端實作的欄位名是 tracking_status（契約寫 status），兩者都收，統一成 status。 */
function normalizeItem(it) {
  var out = {};
  Object.keys(it || {}).forEach(function (k) { out[k] = it[k]; });
  out.status = out.status || out.tracking_status;
  return out;
}

function findTrackingItem(id) {
  if (id === null || id === undefined) return null;
  return state.tracking.items.filter(function (it) { return String(it.id) === String(id); })[0] || null;
}

/** 面板按鈕改追蹤事項狀態，成功後重抓。離線檔不會走到這裡（按鈕不顯示）。 */
function setTrackingAction(id, action, button) {
  if (button) button.disabled = true;
  state.tracking.error = null;
  return api(withAthlete('/api/chat/tracking/' + encodeURIComponent(id)), {
    method: 'POST', body: { action: action }
  }).then(function () {
    return loadTracking();
  }).catch(function (err) {
    state.tracking.error = '更新追蹤事項失敗：' + err.message;
    if (button) button.disabled = false;
    renderTracking();
    renderLog();
  });
}

function trackingButton(label, id, action, primary) {
  var btn = h('button', 'coach-mini-btn' + (primary ? ' coach-mini-btn-primary' : ''), label);
  btn.type = 'button';
  btn.addEventListener('click', function () { setTrackingAction(id, action, btn); });
  return btn;
}

function trackingActions(item) {
  if (isOfflineSnapshot()) return null;
  var row = h('div', 'coach-track-actions');
  if (item.status === 'tracking') {
    row.appendChild(trackingButton('已痊癒', item.id, 'resolve'));
    row.appendChild(trackingButton('不再追蹤', item.id, 'stop'));
  } else if (item.status === 'proposed') {
    row.appendChild(trackingButton('追蹤', item.id, 'accept', true));
    row.appendChild(trackingButton('不用', item.id, 'decline'));
  } else {
    return null;
  }
  return row;
}

function trackingMeta(item) {
  if (item.status === 'proposed') {
    return (shortDate(item.start_date) || shortDate(item.last_mentioned_date)) + ' 提到';
  }
  var parts = [];
  if (item.start_date) parts.push(shortDate(item.start_date) + ' 起');
  if (item.last_followup_date) parts.push('上次追問 ' + shortDate(item.last_followup_date));
  if (item.next_followup_date) parts.push('下次 ' + shortDate(item.next_followup_date));
  return parts.join('・');
}

/** 收合時的一行摘要：左邊事項、右邊下次追問日。 */
function trackingSummary(tracking, proposed, advice) {
  var left = '';
  if (tracking.length > 0) {
    left = '追蹤中：' + tracking[0].note
      + (tracking.length > 1 ? '（另 ' + (tracking.length - 1) + ' 項）' : '')
      + (proposed.length > 0 ? '（+' + proposed.length + ' 待同意）' : '');
  } else if (proposed.length > 0) {
    left = '待同意：' + proposed[0].note
      + (proposed.length > 1 ? '（+' + (proposed.length - 1) + '）' : '');
  } else {
    left = '近期建議 ' + advice.length + ' 則';
  }
  var next = tracking.map(function (t) { return t.next_followup_date; })
    .filter(Boolean).sort()[0];
  return { left: left, right: next ? '下次 ' + shortDate(next) : '' };
}

function renderTracking() {
  var box = $('coach-track');
  if (!box) return;
  clear(box);
  var items = state.tracking.items;
  var advice = state.tracking.advice;
  var tracking = items.filter(function (it) { return it.status === 'tracking'; });
  var proposed = items.filter(function (it) { return it.status === 'proposed'; });
  if (items.length === 0 && advice.length === 0) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  var expanded = state.tracking.expanded;
  box.classList.toggle('is-open', expanded);

  var head = h('button', 'coach-track-head');
  head.type = 'button';
  head.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  head.addEventListener('click', function () {
    state.tracking.expanded = !state.tracking.expanded;
    renderTracking();
  });
  if (expanded) {
    var counts = [];
    if (tracking.length > 0) counts.push('追蹤中 ' + tracking.length + ' 項');
    if (proposed.length > 0) counts.push('待同意 ' + proposed.length + ' 項');
    if (counts.length === 0) counts.push('近期建議');
    head.appendChild(h('span', 'coach-track-left', counts.join('・')));
    head.appendChild(h('span', 'coach-track-right', '收起 ▴'));
  } else {
    var sum = trackingSummary(tracking, proposed, advice);
    head.appendChild(h('span', 'coach-track-left', sum.left));
    head.appendChild(h('span', 'coach-track-right', (sum.right ? sum.right + ' ' : '') + '▾'));
  }
  box.appendChild(head);
  if (!expanded) return;

  var body = h('div', 'coach-track-body');
  tracking.concat(proposed).forEach(function (item) {
    var row = h('div', 'coach-track-item');
    var title = h('div', 'coach-track-title');
    title.appendChild(h('strong', null, item.note));
    title.appendChild(h('span', 'coach-track-badge' + (item.status === 'proposed' ? ' is-proposed' : ''),
      item.status === 'proposed' ? '等你同意' : '追蹤中'));
    row.appendChild(title);
    var meta = trackingMeta(item);
    if (meta) row.appendChild(h('div', 'coach-track-meta', meta));
    // 待同意的 reason 與「等你同意」徽章重複，只顯示追蹤中事項的（例如「30 天沒提起」）
    if (item.status === 'tracking' && item.action_reason && item.action !== 'none') {
      row.appendChild(h('div', 'coach-track-meta', item.action_reason));
    }
    var actions = trackingActions(item);
    if (actions) row.appendChild(actions);
    body.appendChild(row);
  });
  if (state.tracking.error) body.appendChild(h('p', 'coach-track-error', state.tracking.error));

  if (advice.length > 0) {
    body.appendChild(h('div', 'coach-advice-head', '近期建議（30 天內，教練會參考）'));
    advice.forEach(function (a) {
      var rec = h('div', 'coach-advice');
      rec.appendChild(h('div', 'coach-advice-text', a.text));
      rec.appendChild(h('div', 'coach-advice-meta', shortDate(a.date) + ' 分析近況'));
      body.appendChild(rec);
    });
  }
  box.appendChild(body);
}

// ------------------------------------------------------------ 訊息渲染

function toolArgText(input) {
  try {
    var keys = Object.keys(input || {});
    if (keys.length === 0) return '';
    return '(' + keys.map(function (k) {
      return k + '=' + JSON.stringify(input[k]);
    }).join(', ') + ')';
  } catch (e) {
    return '';
  }
}

/** 工具呼叫 chip：預設收合，展開可見模型實際拿到的 JSON。 */
function toolChipNode(tool) {
  var details = h('details', 'coach-tool');
  var summary = h('summary', 'coach-tool-summary');
  summary.appendChild(h('span', 'coach-tool-label', '查了'));
  summary.appendChild(h('code', 'coach-tool-name', tool.name + toolArgText(tool.input)));
  if (tool.isError) summary.appendChild(h('span', 'coach-tool-error', '失敗'));
  else if (tool.result === null) summary.appendChild(h('span', 'coach-tool-pending', '查詢中…'));
  details.appendChild(summary);

  var pre = h('pre', 'coach-tool-json');
  var text = tool.result === null ? '（查詢中）' : String(tool.result);
  if (text.length > MAX_TOOL_JSON_CHARS) {
    text = text.slice(0, MAX_TOOL_JSON_CHARS)
      + '\n…（顯示已截斷，完整內容共 ' + text.length + ' 字）';
  }
  pre.textContent = text;
  details.appendChild(pre);
  return details;
}

/** 工具清單收成一個可展開區塊（分析回合用）：報告才會在第一屏。 */
function toolGroupNode(tools, open) {
  var group = h('details', 'coach-tool-group');
  group.open = !!open;
  group.appendChild(h('summary', 'coach-tool-group-summary', '查了 ' + tools.length + ' 個工具'));
  tools.forEach(function (t) { group.appendChild(toolChipNode(t)); });
  return group;
}

function isSpecialTool(tool) {
  return tool.name === TOOL_PROPOSE || tool.name === TOOL_UPDATE || tool.name === TOOL_FEEL;
}

/** propose_tracking_item → 虛線卡片「新的追蹤事項｜需要你同意」。 */
function proposeCardNode(tool) {
  var res = parseJson(tool.result) || {};
  var item = res.item || {};
  var id = res.id !== undefined ? res.id : (res.item_id !== undefined ? res.item_id : item.id);
  var noteText = res.note || item.note || (tool.input && (tool.input.note || tool.input.text)) || '';
  var card = h('div', 'coach-ask');
  card.appendChild(h('div', 'coach-ask-kicker', '新的追蹤事項｜需要你同意'));
  card.appendChild(h('div', 'coach-ask-note', noteText ? '「' + noteText + '」' : '（教練提議追蹤一件事）'));
  if (tool.isError || res.saved === false) {
    card.appendChild(h('div', 'coach-ask-state', '未建立' + (res.reason ? '：' + res.reason : '')));
    return card;
  }
  var current = findTrackingItem(id);
  if (current && current.status === 'proposed') {
    if (!isOfflineSnapshot()) {
      var btns = h('div', 'coach-ask-actions');
      btns.appendChild(trackingButton('追蹤', current.id, 'accept', true));
      btns.appendChild(trackingButton('不用', current.id, 'decline'));
      card.appendChild(btns);
    } else {
      card.appendChild(h('div', 'coach-ask-state', '等你同意'));
    }
  } else if (current && current.status === 'tracking') {
    card.appendChild(h('div', 'coach-ask-state', '✓ 已開始追蹤'));
  } else if (state.tracking.loaded && id !== undefined && id !== null) {
    card.appendChild(h('div', 'coach-ask-state', '已處理'));
  }
  return card;
}

/** record_daily_feel → 「✓ 已記錄 M/D 體感：疲憊（你說的）」；沒存成時顯示原因。 */
function feelLineNode(tool) {
  var res = parseJson(tool.result);
  var input = tool.input || {};
  var date = (res && res.date) || input.date;
  var feel = (res && res.feel) || input.feel;
  var line = h('div', 'coach-done');
  if (tool.isError || (res && res.saved === false)) {
    line.classList.add('is-muted');
    line.appendChild(document.createTextNode('體感未記錄'
      + (res && res.reason ? '：' + res.reason : '')));
    return line;
  }
  line.appendChild(document.createTextNode('✓ 已記錄 '));
  line.appendChild(h('strong', null, (shortDate(date) ? shortDate(date) + ' ' : '')
    + '體感：' + (FEEL_LABEL[feel] || feel || '')));
  line.appendChild(document.createTextNode('（你說的）'));
  return line;
}

/** update_tracking_item → 一行小字，例如「已追問：左膝外側緊」。 */
function updateLineNode(tool) {
  var input = tool.input || {};
  var res = parseJson(tool.result) || {};
  var id = input.item_id !== undefined ? input.item_id : (input.id !== undefined ? input.id : res.id);
  var current = findTrackingItem(id);
  var noteText = res.note || (res.item && res.item.note) || (current && current.note) || input.note || '';
  var label = UPDATE_LABEL[input.action] || '已更新追蹤事項';
  if (tool.isError || res.saved === false) {
    return h('div', 'coach-done is-muted', '追蹤事項未更新' + (res.reason ? '：' + res.reason : ''));
  }
  return h('div', 'coach-done is-muted', label + (noteText ? '：' + noteText : ''));
}

function specialToolNode(tool) {
  if (tool.result === null && !tool.isError) return null; // 等結果回來再顯示
  if (tool.name === TOOL_PROPOSE) return proposeCardNode(tool);
  if (tool.name === TOOL_FEEL) return feelLineNode(tool);
  return updateLineNode(tool);
}

/** 一則訊息 → 一或多個節點（教練氣泡後面接寫入型工具的結果）。 */
function messageNodes(msg) {
  if (msg.role === 'user') {
    var row = h('div', 'coach-msg coach-msg-user');
    if (msg.kind === 'analysis') {
      row.appendChild(h('span', 'coach-tag', '分析近況'));
    } else {
      row.appendChild(h('div', 'coach-bubble coach-bubble-user', msg.text));
    }
    return [row];
  }

  var nodes = [];
  var tools = msg.tools || [];
  var plain = tools.filter(function (t) { return !isSpecialTool(t); });
  var special = tools.filter(isSpecialTool);
  var waiting = !msg.text && plain.length === 0;

  if (!waiting || special.length === 0) {
    var coachRow = h('div', 'coach-msg coach-msg-coach');
    var bubble = h('div', 'coach-bubble coach-bubble-coach');
    if (msg.kind === 'analysis') {
      if (plain.length > 0) bubble.appendChild(toolGroupNode(plain, false));
    } else {
      plain.forEach(function (tool) { bubble.appendChild(toolChipNode(tool)); });
    }
    if (msg.text) {
      var md = h('div', 'coach-md');
      md.innerHTML = renderMarkdown(msg.text); // 已 escape，見 renderMarkdown
      bubble.appendChild(md);
    } else if (plain.length === 0) {
      bubble.appendChild(h('span', 'coach-typing', msg.pending ? WAITING_TEXT : '（沒有文字回覆）'));
    }
    coachRow.appendChild(bubble);
    nodes.push(coachRow);
  }
  special.forEach(function (tool) {
    var node = specialToolNode(tool);
    if (node) nodes.push(node);
  });
  return nodes;
}

/** 「分析近況」進行中／失敗的卡片，放在目前對話底部。 */
function analysisCardNode() {
  var a = state.analysis;
  var card = h('div', 'coach-progress' + (a.status === 'error' ? ' is-error' : ''));
  if (a.status === 'running') {
    card.appendChild(h('div', 'coach-progress-title', '分析近況進行中…'));
    card.appendChild(h('div', 'coach-progress-meta',
      '已查 ' + a.tools + ' 個工具，通常需要 1～2 分鐘；完成後報告會出現在這則對話裡。'));
  } else {
    card.appendChild(h('div', 'coach-progress-title', '分析失敗'));
    card.appendChild(h('div', 'coach-progress-meta', a.error || '原因不明'));
  }
  a.denied.forEach(function (name) {
    card.appendChild(h('div', 'coach-progress-meta', '已擋下工具 ' + name + '（分析只開放允許的工具）'));
  });
  return card;
}

function renderLog() {
  var log = $('coach-log');
  if (!log) return;
  var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  clear(log);

  if (state.loadError) {
    log.appendChild(h('div', 'coach-error', state.loadError));
  }
  if (state.messages.length === 0 && !state.loadError && !state.analysis) {
    var emptyText;
    if (canChat()) emptyText = '問教練關於你的訓練——他會自己去查資料再回答。';
    else if (isOfflineSnapshot()) emptyText = state.conversations.length === 0
      ? '這份離線快照沒有 AI 教練的對話紀錄。' : '從上方選單選一則對話來看。';
    else emptyText = 'AI 教練暫時無法使用'
      + (state.status && state.status.reason ? '：' + state.status.reason : '') + '。';
    log.appendChild(h('p', 'coach-empty', emptyText));
  }

  var lastDay = null;
  state.messages.forEach(function (msg) {
    var day = localDayKey(msg.createdAt);
    if (day && day !== lastDay) {
      log.appendChild(h('div', 'coach-day', dayLabel(day)));
      lastDay = day;
    }
    messageNodes(msg).forEach(function (node) { log.appendChild(node); });
  });

  if (state.analysis) log.appendChild(analysisCardNode());

  if (state.error) {
    var box = h('div', 'coach-error');
    box.appendChild(h('span', null, state.error));
    if (state.lastSentText) {
      var retry = h('button', 'coach-retry', '重試');
      retry.type = 'button';
      retry.addEventListener('click', retryLast);
      box.appendChild(retry);
    }
    log.appendChild(box);
  }

  if (atBottom || isBusy()) log.scrollTop = log.scrollHeight;
}

function setStreaming(on) {
  state.streaming = on;
  renderBusy();
}

// ------------------------------------------------------------ 對話管理

/**
 * 重抓對話清單。openLatest：目前沒開任何對話時開最近更新的那則（啟動時一律如此）。
 */
function loadConversations(openLatest) {
  return api(withAthlete('/api/chat/conversations')).then(function (data) {
    state.conversations = (data && data.conversations) || [];
    if (openLatest && state.activeId === null && state.conversations.length > 0) {
      return openConversation(state.conversations[0].id);
    }
    renderConversationSelect();
    renderLog();
  }).catch(function (err) {
    state.loadError = '無法載入對話清單：' + err.message;
    renderLog();
  });
}

/**
 * 把後端存的 Anthropic content blocks 攤平成畫面用的訊息模型。
 * 同一回合的多則 assistant 訊息（中間夾著只含 tool_result 的 user 訊息）合併成一個教練氣泡。
 * noResults：離線快照沒有 tool_result，工具 chip 顯示固定說明而不是「查詢中…」。
 */
function messagesFromBlocks(rawMessages, noResults) {
  var out = [];
  var results = {}; // tool_use_id → tool_result block

  rawMessages.forEach(function (m) {
    if (m.role !== 'user' || !Array.isArray(m.content)) return;
    m.content.forEach(function (block) {
      if (block && block.type === 'tool_result') results[block.tool_use_id] = block;
    });
  });

  var coach = null;     // 正在累積的教練訊息
  var turnKind = 'chat';
  rawMessages.forEach(function (m) {
    var blocks = Array.isArray(m.content) ? m.content : [];
    if (m.role === 'user') {
      // 只含 tool_result 的 user 訊息是協定的一部分，不是使用者打的字
      var texts = blocks.filter(function (b) { return b && b.type === 'text'; });
      if (texts.length === 0) return;
      var isAnalysis = m.kind === 'analysis'
        || String(texts[0].text || '').replace(/^\s+/, '').indexOf('〔分析近況〕') === 0;
      turnKind = isAnalysis ? 'analysis' : 'chat';
      coach = null;
      out.push({
        role: 'user',
        kind: turnKind,
        text: visibleUserText(texts.map(function (b) { return b.text; }).join('\n')),
        createdAt: m.created_at
      });
      return;
    }
    // assistant
    if (!coach) {
      coach = {
        role: 'coach', text: '', tools: [], createdAt: m.created_at,
        kind: m.kind === 'analysis' ? 'analysis' : turnKind
      };
      out.push(coach);
    }
    blocks.forEach(function (b) {
      if (!b) return;
      if (b.type === 'text') {
        if (b.text) coach.text += (coach.text ? '\n\n' : '') + b.text;
      } else if (b.type === 'tool_use') {
        var res = results[b.id];
        coach.tools.push({
          id: b.id,
          name: toolName(b.name),
          input: b.input || {},
          result: res ? res.content : (noResults ? OFFLINE_TOOL_RESULT : null),
          isError: !!(res && res.is_error)
        });
      }
      // thinking 區塊刻意不顯示（D.4）
    });
  });
  // tool_result 的 content 也可能是 content block 陣列，統一轉成字串
  out.forEach(function (msg) {
    (msg.tools || []).forEach(function (t) {
      if (Array.isArray(t.result)) {
        t.result = t.result.map(function (b) { return b && b.text ? b.text : ''; }).join('');
      }
    });
  });
  return out.filter(function (msg) {
    return msg.role === 'user' || msg.text || msg.tools.length > 0;
  });
}

function openConversation(id) {
  state.activeId = id;
  state.error = null;
  state.loadError = null;
  return api(withAthlete('/api/chat/conversations/' + id)).then(function (data) {
    state.messages = messagesFromBlocks((data && data.messages) || [], isOfflineSnapshot());
    renderConversationSelect();
    renderLog();
    var log = $('coach-log');
    if (log) log.scrollTop = log.scrollHeight; // 換對話一律看最新的
  }).catch(function (err) {
    state.loadError = '無法載入對話：' + err.message;
    renderLog();
  });
}

/** 「＋ 新對話」：只切成空白草稿，第一次送出時才建立（避免留下空對話）。 */
function startDraftConversation() {
  if (isBusy()) return;
  state.activeId = null;
  state.messages = [];
  state.error = null;
  state.loadError = null;
  state.lastSentText = null;
  if (state.analysis && state.analysis.status !== 'running') state.analysis = null;
  renderConversationSelect();
  renderLog();
  var input = $('coach-input');
  if (input) input.focus();
}

function createConversation() {
  return api(withAthlete('/api/chat/conversations'), { method: 'POST' })
    .then(function (conv) {
      state.conversations.unshift(conv);
      state.activeId = conv.id;
      renderConversationSelect();
      return conv;
    });
}

function deleteActiveConversation() {
  if (state.activeId === null || isBusy()) return;
  if (!window.confirm('刪除這則對話？此動作無法復原。')) return;
  var id = state.activeId;
  api(withAthlete('/api/chat/conversations/' + id), { method: 'DELETE' })
    .then(function () {
      state.conversations = state.conversations.filter(function (c) { return c.id !== id; });
      state.activeId = null;
      state.messages = [];
      if (state.conversations.length > 0) return openConversation(state.conversations[0].id);
      renderConversationSelect();
      renderLog();
    })
    .catch(function (err) {
      state.error = '刪除失敗：' + err.message;
      renderLog();
    });
}

// ------------------------------------------------------------ 串流

/**
 * 解析 SSE 位元流。EventSource 不能用（它只支援 GET），所以自己 fetch(POST)
 * 再逐行拆 `event:` / `data:`。
 * @param {ReadableStreamDefaultReader} reader
 * @param {function(string, Object)} onEvent
 */
function pumpSSE(reader, onEvent) {
  var decoder = new TextDecoder();
  var buffer = '';

  function handleChunk(chunk) {
    buffer += chunk;
    // SSE 以空行分隔事件；\r\n\r\n 是為了容忍中介層改寫換行
    var parts = buffer.split(/\r?\n\r?\n/);
    buffer = parts.pop(); // 最後一段可能不完整，留到下次
    parts.forEach(function (block) {
      var eventName = 'message';
      var dataLines = [];
      block.split(/\r?\n/).forEach(function (line) {
        if (line.indexOf('event:') === 0) eventName = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) dataLines.push(line.slice(5).trim());
      });
      if (dataLines.length === 0) return;
      var payload;
      try {
        payload = JSON.parse(dataLines.join('\n'));
      } catch (e) {
        return; // 壞掉的一則事件不該讓整條串流停掉
      }
      onEvent(eventName, payload);
    });
  }

  function step() {
    return reader.read().then(function (res) {
      if (res.done) {
        if (buffer.trim()) handleChunk('\n\n'); // 沖掉殘留
        return;
      }
      handleChunk(decoder.decode(res.value, { stream: true }));
      return step();
    });
  }
  return step();
}

function submitMessage(rawText) {
  var text = String(rawText || '').trim();
  if (!text || isBusy()) return;
  if (!canChat()) return;

  var input = $('coach-input');
  if (input) input.value = '';
  state.error = null;
  if (state.analysis && state.analysis.status !== 'running') state.analysis = null;

  var ctx = contextLine(viewContext());
  var payloadText = ctx ? (text + '\n' + ctx) : text;
  state.lastSentText = payloadText;

  var now = new Date().toISOString();
  state.messages.push({ role: 'user', kind: 'chat', text: text, createdAt: now });
  var coachMsg = { role: 'coach', kind: 'chat', text: '', tools: [], createdAt: now, pending: true };
  state.messages.push(coachMsg);
  setStreaming(true);
  renderLog();

  // 沒有對話（第一次使用或「＋ 新對話」的草稿）：送出時才建立
  var ensure = state.activeId === null ? createConversation() : Promise.resolve(null);
  ensure.then(function () {
    return sendTurn(payloadText, coachMsg);
  }).catch(function (err) {
    dropEmptyCoach();
    state.error = '送出失敗：' + err.message;
    setStreaming(false);
    renderLog();
  });
}

/** 回合沒送成（409、連線錯誤）時，拿掉還沒有任何內容的教練空殼。 */
function dropEmptyCoach() {
  var last = state.messages[state.messages.length - 1];
  if (last && last.role === 'coach' && !last.text && last.tools.length === 0) state.messages.pop();
}

function retryLast() {
  if (!state.lastSentText || isBusy() || state.activeId === null) return;
  state.error = null;
  // 上一回合未落地（後端 rollback），重試就是重送同一段文字。
  // 畫面上把上次那則失敗的教練訊息換成新的空殼。
  var last = state.messages[state.messages.length - 1];
  if (last && last.role === 'coach') state.messages.pop();
  var coachMsg = {
    role: 'coach', kind: 'chat', text: '', tools: [],
    createdAt: new Date().toISOString(), pending: true
  };
  state.messages.push(coachMsg);
  setStreaming(true);
  renderLog();
  sendTurn(state.lastSentText, coachMsg);
}

function applyEvent(name, data, coachMsg) {
  if (name === 'text') {
    coachMsg.text += data.delta || '';
  } else if (name === 'tool_use') {
    coachMsg.tools.push({
      id: data.id, name: toolName(data.name), input: data.input || {},
      result: null, isError: false
    });
  } else if (name === 'tool_result') {
    var tool = coachMsg.tools.filter(function (t) { return t.id === data.tool_use_id; })[0];
    if (tool) {
      tool.result = data.content;
      tool.isError = !!data.is_error;
      // 教練剛提議或更新了追蹤事項：重抓清單，卡片按鈕與橫條才會是最新狀態
      if (tool.name === TOOL_PROPOSE || tool.name === TOOL_UPDATE) loadTracking();
    }
  } else if (name === 'error') {
    // 保留已收到的文字（D.4），另外顯示錯誤與重試鈕
    state.error = data.message || '對話發生錯誤。';
  } else if (name === 'done') {
    state.lastSentText = null;
  }
  renderLog();
}

function sendTurn(payloadText, coachMsg) {
  var path = withAthlete('/api/chat/conversations/' + state.activeId + '/messages');

  function finish() {
    coachMsg.pending = false;
    setStreaming(false);
    refreshTitles();
    renderLog();
  }

  if (isMock() && window.MOCK_API.chatStream) {
    return window.MOCK_API.chatStream(payloadText, function (name, data) {
      applyEvent(name, data, coachMsg);
    }).then(finish, function (err) {
      coachMsg.pending = false;
      dropEmptyCoach();
      state.error = err.message;
      setStreaming(false);
      renderLog();
    });
  }

  return fetch('.' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ text: payloadText })
  }).then(function (res) {
    if (!res.ok) return streamError(res);
    // 後端在收到請求時就建立標題（N-36 決策 5），不等教練回答就能更新下拉選單
    refreshTitles();
    if (!res.body) throw new Error('這個瀏覽器不支援串流讀取。');
    return pumpSSE(res.body.getReader(), function (name, data) {
      applyEvent(name, data, coachMsg);
    });
  }).then(function () {
    finish();
    loadTracking(); // 這一回合可能新增或結案了追蹤事項
  }, function (err) {
    coachMsg.pending = false;
    dropEmptyCoach();
    state.error = err.message;
    setStreaming(false);
    renderLog();
  });
}

/** 重抓清單讓下拉顯示正確標題（不切換目前對話）。 */
function refreshTitles() {
  api(withAthlete('/api/chat/conversations')).then(function (data) {
    state.conversations = (data && data.conversations) || [];
    renderConversationSelect();
  }).catch(function () { /* 標題只是顯示，抓不到不影響對話 */ });
}

// ------------------------------------------------------------ 分析近況（併入對話，N-36 決策 4）
//
// 由後端 /api/analysis 在背景啟動 Claude Code 無互動 session 產生，報告存成**目前對話**
// 的一回合（學員訊息為〔分析近況〕）。前端只顯示進行中卡片，完成後重新載入對話，
// 報告就跟一般回覆一樣出現在訊息串裡、可以直接在下面追問。

function applyAnalysisEvent(name, data) {
  var a = state.analysis;
  if (!a) return;
  if (name === 'start') {
    if (data.conversation_id !== undefined && data.conversation_id !== null) {
      a.conversationId = data.conversation_id;
    }
  } else if (name === 'tool_use') {
    a.tools += 1;
  } else if (name === 'denied') {
    a.denied.push(data.tool);
  } else if (name === 'done') {
    if (data.conversation_id !== undefined && data.conversation_id !== null) {
      a.conversationId = data.conversation_id;
    }
    if (data.is_error) {
      a.status = 'error';
      a.error = data.report || '分析沒有正常完成';
    } else {
      a.status = 'done';
    }
  } else if (name === 'error') {
    a.status = 'error';
    a.error = data.message;
  }
  renderLog();
}

/**
 * 外部入口：打開面板並在目前對話跑一次「分析近況」（面板標題列的按鈕）。
 * 不能跑或失敗時把原因顯示在訊息串裡，不 reject（呼叫端只需在完成後還原按鈕）。
 * @returns {Promise}
 */
function runAnalysis() {
  openCoachPanel();
  if (isBusy()) return Promise.resolve();
  state.analysis = null; // 清掉上一次失敗的卡片
  state.error = null;

  return api('/api/analysis/status').then(function (status) {
    if (!status.client_allowed) throw new Error('AI 分析僅限本機使用');
    if (!status.enabled) throw new Error('AI 分析無法使用：' + (status.reason || '未知原因'));
    if (status.running) throw new Error(BUSY_TEXT);

    state.error = null;
    state.analysis = { tools: 0, denied: [], status: 'running', error: null, conversationId: state.activeId };
    renderBusy();
    renderLog();
    // 在「＋ 新對話」的草稿上按：先建立對話，報告才會放進這則新的，而不是後端挑的最近一則
    if (state.activeId === null && state.conversations.length > 0) return createConversation();
    return null;
  }).then(function () {
    var path = '/api/analysis'
      + (state.activeId !== null ? '?conversation_id=' + encodeURIComponent(state.activeId) : '');
    return fetch('.' + withAthlete(path), {
      method: 'POST',
      headers: { Accept: 'text/event-stream' }
    });
  }).then(function (res) {
    if (!res.ok) return streamError(res);
    if (!res.body) throw new Error('這個瀏覽器不支援串流讀取。');
    return pumpSSE(res.body.getReader(), applyAnalysisEvent);
  }).then(function () {
    var a = state.analysis;
    if (a && a.status === 'running') {
      a.status = 'error';
      a.error = '連線中斷，未收到完整結果';
    }
    return finishAnalysis();
  }, function (err) {
    if (!state.analysis) {
      // 還沒開始就失敗（status 檢查）：說明放在訊息串底部，手機全螢幕時也看得到
      state.error = '無法開始分析：' + err.message;
      state.lastSentText = null;
      renderLog();
      return;
    }
    state.analysis.status = 'error';
    state.analysis.error = err.message;
    finishAnalysis();
  });
}

/** 分析結束：成功就清掉卡片、重新載入該對話與清單；失敗保留卡片顯示原因。 */
function finishAnalysis() {
  var a = state.analysis;
  var convId = a && a.conversationId !== undefined && a.conversationId !== null
    ? a.conversationId : state.activeId;
  if (a && a.status === 'done') state.analysis = null;
  renderBusy();
  loadTracking(); // 近期建議多了一份
  return loadConversations(false).then(function () {
    if (convId !== null && convId !== undefined) return openConversation(convId);
    renderLog();
  });
}

// ------------------------------------------------------------ 啟動

function init() {
  if (!$('coach-panel')) return; // 面板不存在（理論上不會發生）就什麼都不做

  api('/api/chat/status').then(function (status) {
    state.status = status;
    if (!status.client_allowed) {
      renderDisabled('AI 教練僅限本機使用。要讓區網裝置使用，'
        + '請在啟動 server 的環境設定 RC_CHAT_ALLOW_PRIVATE_NETWORK=1。');
      return;
    }
    if (!buildPanel()) return;
    renderComposer();
    renderLog();
    loadTracking();
    // 啟動時一律開最近更新的對話（N-36 決策 7：延續同一則對話）
    loadConversations(true);
  }).catch(function (err) {
    renderDisabled('無法取得 AI 教練狀態：' + err.message);
  });

  // 切 view 時更新建議問題（路由由 app.js 負責，這裡只跟著換 chips）
  window.addEventListener('hashchange', function () {
    if (canChat()) renderChips();
  });
}

// init 由 main.js 在 App 殼就緒後呼叫；runAnalysis 供面板標題列的「分析近況」使用
export {
  init as initCoach,
  runAnalysis,
  renderMarkdown,
  contextLine,
  suggestionsFor
};
