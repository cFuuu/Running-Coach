/**
 * coach.js — AI 教練聊天面板（DASHBOARD_TASKS.md D.4／S3）
 *
 * 【架構契約】
 *   本檔只負責聊天面板：對話管理、SSE 串流、訊息渲染。
 *   透過 core/state.js 的 getViewContext() 讀 App 的檢視脈絡、只讀 athleteId，
 *   **不改 App 的 state**，也不 import charts.js。
 *
 * 【D.1 硬約束在本檔的含義】
 *   - 面板不在前端拼數據餵模型：送出的只有「使用者打的字」＋一行〔檢視脈絡〕
 *     （view／session_id／range 等識別資訊），數據一律由模型自己呼叫 MCP 工具取得，
 *     那些工具的回傳才帶信賴度（PLAN.md §7.3 三欄位契約）。
 *   - 工具呼叫顯示成可展開 chip：把行為約束 4「回述事實清單」變成介面上看得見的
 *     東西——使用者可以自己核對教練查了什麼、拿到什麼。
 *   - 只有唯讀工具會出現（後端 READ_ONLY_TOOL_NAMES 把關），面板沒有寫入路徑。
 *
 * 【零建置、無 CDN】
 *   Markdown 是自寫的極簡子集（段落／清單／粗體／行內碼／標題），先 escape 再轉，
 *   不引任何函式庫。
 */

import { $, clear, h } from './core/dom.js';
import { state as appState, getViewContext } from './core/state.js';
import { openCoachPanel } from './shell/coach-frame.js';

// ------------------------------------------------------------ 狀態

var state = {
  status: null,        // /api/chat/status 的回傳（null = 還沒問到）
  conversations: [],
  activeId: null,
  messages: [],        // 畫面上的訊息（見下方「訊息模型」）
  streaming: false,
  lastSentText: null,  // 供 error 後重試：該回合未落地，重試＝重送
  loadError: null
};

/**
 * 訊息模型（畫面用，不等於後端存的 Anthropic content blocks）：
 *   {role: 'user',   text: string}
 *   {role: 'coach',  text: string, tools: [{id, name, input, result, isError}]}
 * 教練訊息的 tools 依 tool_use 事件建立、由 tool_result 事件補上結果。
 * thinking 區塊不顯示（D.4）。
 */

var MAX_TOOL_JSON_CHARS = 20000; // chip 展開時的顯示上限，避免超長 JSON 卡住畫面

// ------------------------------------------------------------ 小工具

function isMock() {
  return !!window.MOCK_API;
}

/** 目前的 athlete_id（沿用 app.js 已解析好的值，避免兩處各自問一次 /api/meta）。 */
function athleteParam() {
  var id = appState.athleteId;
  return (id === null || id === undefined) ? '' : '?athlete_id=' + encodeURIComponent(id);
}

function withAthlete(path) {
  var p = athleteParam();
  if (!p) return path;
  return path + (path.indexOf('?') === -1 ? p : '&' + p.slice(1));
}

/** 一般（非串流）請求。mock 模式改走 mock-data.js。 */
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
      throw new Error('伺服器回應不是 JSON（HTTP ' + res.status + '）');
    }).then(function (data) {
      if (!res.ok) {
        throw new Error((data && data.detail) || ('HTTP ' + res.status));
      }
      return data;
    });
  });
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
    case 'today':
      return ['今天照課表練還是調整？', '最近一場練得如何？'];
    case 'calendar':
      return ['這週課表跟實際對得上嗎？'];
    case 'sessions':
      return ctx.sessionId
        ? ['解讀這場訓練的品質', '心率漂移的數值代表什麼？']
        : [];
    case 'load':
      return ['目前負荷該增量、維持還是降量？'];
    case 'wellness':
      return ['近期身體數據有什麼要注意？'];
    default:
      return [];
  }
}

/**
 * 附在訊息末尾的〔檢視脈絡〕行——只給識別資訊（view／id／range），
 * **不貼任何數據**：模型要數字就自己呼叫工具，那樣才帶得到信賴度。
 * @returns {string} 空字串表示沒有可附的脈絡
 */
function contextLine(ctx) {
  if (!ctx) return '';
  var parts = ['view=' + ctx.view];
  if (ctx.sessionId) parts.push('session_id=' + ctx.sessionId);
  if (ctx.month) parts.push('month=' + ctx.month);
  if (ctx.range && ctx.view !== 'today' && ctx.view !== 'calendar') {
    parts.push('range=' + ctx.range);
  }
  if (ctx.date) parts.push('date=' + ctx.date);
  return '〔檢視脈絡〕' + parts.join(' ');
}

function viewContext() {
  return getViewContext();
}

// ------------------------------------------------------------ 版面

/** 建立面板骨架（取代 S1 的 placeholder）。只做一次。 */
function buildPanel() {
  var body = $('coach-body');
  if (!body) return false;
  clear(body);

  var wrap = h('div', 'coach-layout');

  // 對話管理列
  var bar = h('div', 'coach-convbar');
  var select = h('select', 'coach-conv-select');
  select.id = 'coach-conv-select';
  select.setAttribute('aria-label', '選擇對話');
  select.addEventListener('change', function () {
    var id = parseInt(select.value, 10);
    if (!isNaN(id)) openConversation(id);
  });
  var newBtn = h('button', 'coach-icon-btn', '＋');
  newBtn.type = 'button';
  newBtn.title = '新對話';
  newBtn.setAttribute('aria-label', '新對話');
  newBtn.addEventListener('click', startNewConversation);
  var delBtn = h('button', 'coach-icon-btn', '🗑');
  delBtn.type = 'button';
  delBtn.id = 'coach-delete';
  delBtn.title = '刪除這則對話';
  delBtn.setAttribute('aria-label', '刪除這則對話');
  delBtn.addEventListener('click', deleteActiveConversation);
  bar.appendChild(select);
  bar.appendChild(newBtn);
  bar.appendChild(delBtn);

  // 訊息區
  var log = h('div', 'coach-log');
  log.id = 'coach-log';
  log.setAttribute('role', 'log');
  log.setAttribute('aria-live', 'polite');

  // 建議問題
  var chips = h('div', 'coach-chips');
  chips.id = 'coach-chips';

  // 輸入區
  var form = h('form', 'coach-form');
  form.id = 'coach-form';
  var input = h('textarea', 'coach-input');
  input.id = 'coach-input';
  input.rows = 2;
  input.placeholder = '問教練…（Enter 送出，Shift+Enter 換行）';
  input.setAttribute('aria-label', '輸入問題');
  input.addEventListener('keydown', function (evt) {
    if (evt.key === 'Enter' && !evt.shiftKey) {
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

  wrap.appendChild(bar);
  wrap.appendChild(log);
  wrap.appendChild(chips);
  wrap.appendChild(form);
  body.appendChild(wrap);
  return true;
}

/** 面板不可用時（沒 key／非本機）的說明，並鎖住輸入。 */
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
  if (state.conversations.length === 0) {
    var empty = h('option', null, '（尚無對話）');
    empty.value = '';
    select.appendChild(empty);
    select.disabled = true;
  } else {
    select.disabled = false;
    state.conversations.forEach(function (c) {
      var opt = h('option', null, c.title || '（未命名對話）');
      opt.value = String(c.id);
      if (c.id === state.activeId) opt.selected = true;
      select.appendChild(opt);
    });
  }
  var del = $('coach-delete');
  if (del) del.disabled = state.activeId === null;
}

function renderChips() {
  var node = $('coach-chips');
  if (!node) return;
  clear(node);
  if (state.streaming) return;
  suggestionsFor(viewContext()).forEach(function (text) {
    var chip = h('button', 'coach-chip', text);
    chip.type = 'button';
    chip.addEventListener('click', function () { submitMessage(text); });
    node.appendChild(chip);
  });
}

/** 工具呼叫 chip：預設收合，展開可見模型實際拿到的 JSON。 */
function toolChipNode(tool) {
  var details = h('details', 'coach-tool');
  var summary = h('summary', 'coach-tool-summary');
  var argText = '';
  try {
    var keys = Object.keys(tool.input || {});
    if (keys.length > 0) {
      argText = '(' + keys.map(function (k) {
        return k + '=' + JSON.stringify(tool.input[k]);
      }).join(', ') + ')';
    }
  } catch (e) {
    argText = '';
  }
  summary.appendChild(h('span', 'coach-tool-label', '查了'));
  summary.appendChild(h('code', 'coach-tool-name', tool.name + argText));
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

function messageNode(msg) {
  var row = h('div', 'coach-msg coach-msg-' + msg.role);
  if (msg.role === 'user') {
    row.appendChild(h('div', 'coach-bubble coach-bubble-user', msg.text));
    return row;
  }
  var bubble = h('div', 'coach-bubble coach-bubble-coach');
  (msg.tools || []).forEach(function (tool) {
    bubble.appendChild(toolChipNode(tool));
  });
  if (msg.text) {
    var md = h('div', 'coach-md');
    md.innerHTML = renderMarkdown(msg.text); // 已 escape，見 renderMarkdown
    bubble.appendChild(md);
  } else if (!msg.tools || msg.tools.length === 0) {
    bubble.appendChild(h('span', 'coach-typing', '教練思考中…'));
  }
  row.appendChild(bubble);
  return row;
}

function renderLog() {
  var log = $('coach-log');
  if (!log) return;
  var atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  clear(log);

  if (state.loadError) {
    log.appendChild(h('div', 'coach-error', state.loadError));
  }
  if (state.messages.length === 0 && !state.loadError) {
    log.appendChild(h('p', 'coach-empty',
      '問教練關於你的訓練——他會自己去查資料再回答。'));
  }
  state.messages.forEach(function (msg) {
    log.appendChild(messageNode(msg));
  });

  if (state.error) {
    var box = h('div', 'coach-error');
    box.appendChild(h('span', null, state.error));
    var retry = h('button', 'coach-retry', '重試');
    retry.type = 'button';
    retry.addEventListener('click', retryLast);
    box.appendChild(retry);
    log.appendChild(box);
  }

  if (atBottom || state.streaming) log.scrollTop = log.scrollHeight;
}

function setStreaming(on) {
  state.streaming = on;
  var send = $('coach-send');
  var input = $('coach-input');
  if (send) {
    send.disabled = on;
    send.textContent = on ? '回覆中…' : '送出';
  }
  if (input) input.disabled = on;
  renderChips();
}

// ------------------------------------------------------------ 對話管理

function loadConversations() {
  return api(withAthlete('/api/chat/conversations')).then(function (data) {
    state.conversations = (data && data.conversations) || [];
    if (state.activeId === null && state.conversations.length > 0) {
      return openConversation(state.conversations[0].id);
    }
    renderConversationSelect();
  }).catch(function (err) {
    state.loadError = '無法載入對話清單：' + err.message;
    renderLog();
  });
}

/** 把後端存的 Anthropic content blocks 攤平成畫面用的訊息模型。 */
function messagesFromBlocks(rawMessages) {
  var out = [];
  var pendingResults = {}; // tool_use_id → {content, is_error}

  // 先掃一遍收集 tool_result（它們在下一則 user 訊息裡）
  rawMessages.forEach(function (m) {
    if (m.role !== 'user' || !Array.isArray(m.content)) return;
    m.content.forEach(function (block) {
      if (block && block.type === 'tool_result') {
        pendingResults[block.tool_use_id] = block;
      }
    });
  });

  rawMessages.forEach(function (m) {
    var blocks = Array.isArray(m.content) ? m.content : [];
    if (m.role === 'user') {
      // 只含 tool_result 的 user 訊息是協定的一部分，不是使用者打的字
      var texts = blocks.filter(function (b) { return b && b.type === 'text'; });
      if (texts.length === 0) return;
      out.push({ role: 'user', text: texts.map(function (b) { return b.text; }).join('\n') });
      return;
    }
    // assistant
    var text = '';
    var tools = [];
    blocks.forEach(function (b) {
      if (!b) return;
      if (b.type === 'text') text += b.text;
      else if (b.type === 'tool_use') {
        var res = pendingResults[b.id];
        tools.push({
          id: b.id,
          name: b.name,
          input: b.input || {},
          result: res ? res.content : null,
          isError: !!(res && res.is_error)
        });
      }
      // thinking 區塊刻意不顯示（D.4）
    });
    if (text || tools.length > 0) out.push({ role: 'coach', text: text, tools: tools });
  });
  return out;
}

function openConversation(id) {
  state.activeId = id;
  state.error = null;
  return api(withAthlete('/api/chat/conversations/' + id)).then(function (data) {
    state.messages = messagesFromBlocks((data && data.messages) || []);
    renderConversationSelect();
    renderLog();
  }).catch(function (err) {
    state.loadError = '無法載入對話：' + err.message;
    renderLog();
  });
}

function startNewConversation() {
  return api(withAthlete('/api/chat/conversations'), { method: 'POST' })
    .then(function (conv) {
      state.conversations.unshift(conv);
      state.activeId = conv.id;
      state.messages = [];
      state.error = null;
      renderConversationSelect();
      renderLog();
      var input = $('coach-input');
      if (input) input.focus();
      return conv;
    });
}

function deleteActiveConversation() {
  if (state.activeId === null) return;
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
  if (!text || state.streaming) return;
  if (!state.status || !state.status.enabled || !state.status.client_allowed) return;

  var input = $('coach-input');
  if (input) input.value = '';
  state.error = null;

  var ensure = state.activeId === null
    ? startNewConversation()
    : Promise.resolve(null);

  ensure.then(function () {
    var ctx = contextLine(viewContext());
    var payloadText = ctx ? (text + '\n' + ctx) : text;
    state.lastSentText = payloadText;

    state.messages.push({ role: 'user', text: text });
    var coachMsg = { role: 'coach', text: '', tools: [] };
    state.messages.push(coachMsg);
    setStreaming(true);
    renderLog();

    return sendTurn(payloadText, coachMsg);
  }).catch(function (err) {
    state.error = '送出失敗：' + err.message;
    setStreaming(false);
    renderLog();
  });
}

function retryLast() {
  if (!state.lastSentText || state.streaming) return;
  state.error = null;
  // 上一回合未落地（後端 rollback），重試就是重送同一段文字。
  // 畫面上把上次那則失敗的教練訊息換成新的空殼。
  var last = state.messages[state.messages.length - 1];
  if (last && last.role === 'coach') state.messages.pop();
  var coachMsg = { role: 'coach', text: '', tools: [] };
  state.messages.push(coachMsg);
  setStreaming(true);
  renderLog();
  sendTurn(state.lastSentText, coachMsg).catch(function (err) {
    state.error = '重試失敗：' + err.message;
    setStreaming(false);
    renderLog();
  });
}

function applyEvent(name, data, coachMsg) {
  if (name === 'text') {
    coachMsg.text += data.delta || '';
  } else if (name === 'tool_use') {
    coachMsg.tools.push({
      id: data.id, name: data.name, input: data.input || {},
      result: null, isError: false
    });
  } else if (name === 'tool_result') {
    var tool = coachMsg.tools.filter(function (t) { return t.id === data.tool_use_id; })[0];
    if (tool) {
      tool.result = data.content;
      tool.isError = !!data.is_error;
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

  if (isMock() && window.MOCK_API.chatStream) {
    return window.MOCK_API.chatStream(payloadText, function (name, data) {
      applyEvent(name, data, coachMsg);
    }).then(function () {
      setStreaming(false);
      refreshTitles();
      renderLog();
    });
  }

  return fetch('.' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ text: payloadText })
  }).then(function (res) {
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        throw new Error((data && data.detail) || ('HTTP ' + res.status));
      });
    }
    if (!res.body) throw new Error('這個瀏覽器不支援串流讀取。');
    return pumpSSE(res.body.getReader(), function (name, data) {
      applyEvent(name, data, coachMsg);
    });
  }).then(function () {
    setStreaming(false);
    refreshTitles();
    renderLog();
  }, function (err) {
    state.error = err.message;
    setStreaming(false);
    renderLog();
  });
}

/** 首則訊息送出後後端會補標題，重抓清單讓下拉顯示正確名稱。 */
function refreshTitles() {
  api(withAthlete('/api/chat/conversations')).then(function (data) {
    state.conversations = (data && data.conversations) || [];
    renderConversationSelect();
  }).catch(function () { /* 標題只是顯示，抓不到不影響對話 */ });
}

// ------------------------------------------------------------ AI 分析報告（總覽按鈕）
//
// 由後端 /api/analysis 在背景啟動 Claude Code 無互動 session 產生（不需要
// ANTHROPIC_API_KEY，因此聊天停用時分析仍可用）。報告顯示在面板裡獨立的
// 「分析報告」畫面（#coach-analysis），解讀仍只出現在面板中（D.1）。
// 報告由後端存進 analysis_reports，下拉選單可回看過去的分析。

var analysis = {
  reports: [],      // 過去的分析清單（/api/analysis）
  current: null,    // {id, created_at, status, tools:[], denied:[], text, report, error}
  running: false
};

function showAnalysisView(on) {
  var view = $('coach-analysis');
  var body = $('coach-body');
  if (view) view.hidden = !on;
  if (body) body.hidden = on;
}

/** 瀏覽器當地時間的 ISO 字串（與後端存的 created_at 同為當地時間，不可用 toISOString 的 UTC）。 */
function localIsoNow() {
  var d = new Date();
  var p2 = function (n) { return String(n).padStart(2, '0'); };
  return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate())
    + 'T' + p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds());
}

/** 清單預覽：去掉 markdown 標記（# 標題、> 引言、清單符號），只留文字。 */
function plainPreview(text) {
  return String(text || '').replace(/^[\s#>*\-]+/, '').replace(/[*_`]/g, '');
}

function formatReportTime(iso) {
  if (!iso) return '';
  return iso.slice(5, 10).replace('-', '/') + ' ' + iso.slice(11, 16);
}

function loadAnalysisList() {
  return api(withAthlete('/api/analysis')).then(function (data) {
    analysis.reports = data.reports || [];
    renderAnalysis();
  }).catch(function () { /* 清單抓不到不影響正在看的報告 */ });
}

function openReport(id) {
  return api(withAthlete('/api/analysis/' + id)).then(function (r) {
    analysis.current = {
      id: r.id,
      created_at: r.created_at,
      status: r.status,
      // 保存的報告只記工具名稱與參數，不存查詢原文（避免把整包資料重複存一份）
      tools: (r.tools || []).map(function (t) {
        return { name: t.name, input: t.input, isError: t.is_error, result: '（已保存的報告不含查詢原文）' };
      }),
      denied: [],
      text: '',
      report: r.report_text,
      error: r.error
    };
    renderAnalysis();
  });
}

function renderAnalysis() {
  var view = $('coach-analysis');
  if (!view) return;
  clear(view);

  var bar = h('div', 'coach-convbar');
  var select = h('select', 'coach-conv-select');
  select.setAttribute('aria-label', '選擇分析報告');
  if (analysis.reports.length === 0) {
    select.appendChild(h('option', null, '尚無分析報告'));
    select.disabled = true;
  }
  analysis.reports.forEach(function (r) {
    var opt = h('option', null, formatReportTime(r.created_at) + '　'
      + (r.status === 'done' ? (plainPreview(r.preview) || '分析報告') : r.status === 'error' ? '（失敗）' : '（進行中）'));
    opt.value = String(r.id);
    if (analysis.current && analysis.current.id === r.id) opt.selected = true;
    select.appendChild(opt);
  });
  select.disabled = select.disabled || analysis.running;
  select.addEventListener('change', function () {
    var id = parseInt(select.value, 10);
    if (!isNaN(id)) openReport(id);
  });
  var back = h('button', 'coach-icon-btn coach-analysis-back', '返回對話');
  back.type = 'button';
  back.addEventListener('click', function () { showAnalysisView(false); });
  bar.appendChild(select);
  bar.appendChild(back);
  view.appendChild(bar);

  var log = h('div', 'coach-log coach-analysis-log');
  log.setAttribute('aria-live', 'polite');
  var cur = analysis.current;
  if (!cur) {
    log.appendChild(h('p', 'coach-placeholder', '按總覽的「AI 分析」按鈕產生一份分析，或從上方選單回看過去的報告。'));
    view.appendChild(log);
    return;
  }

  var meta = h('div', 'coach-analysis-meta');
  meta.appendChild(h('strong', null, '最近一週訓練與身體狀況分析'));
  meta.appendChild(h('span', null, formatReportTime(cur.created_at)
    + (cur.status === 'running' ? '　分析中…' : '')));
  log.appendChild(meta);

  if (cur.tools.length > 0) {
    // 工具清單收成一個可展開的區塊：分析中展開看進度，完成後收起讓報告在第一屏
    var group = h('details', 'coach-bubble coach-bubble-coach coach-analysis-tools');
    group.open = cur.status === 'running';
    group.appendChild(h('summary', 'coach-analysis-tools-summary',
      '查了 ' + cur.tools.length + ' 個工具' + (cur.status === 'running' ? '（進行中）' : '（點開看明細）')));
    cur.tools.forEach(function (t) { group.appendChild(toolChipNode(t)); });
    log.appendChild(group);
  }
  cur.denied.forEach(function (name) {
    log.appendChild(h('p', 'coach-analysis-note', '已擋下寫入型工具 ' + name + '（分析只開放唯讀查詢）'));
  });

  if (cur.status === 'running') {
    log.appendChild(h('p', 'coach-analysis-note',
      '正在查資料與分析（已查 ' + cur.tools.length + ' 個工具），通常需要 1～2 分鐘。'));
  }
  var reportText = cur.report || cur.text;
  if (reportText) {
    var bubble = h('div', 'coach-bubble coach-bubble-coach');
    var md = h('div', 'coach-md');
    md.innerHTML = renderMarkdown(reportText); // 已 escape，見 renderMarkdown
    bubble.appendChild(md);
    log.appendChild(bubble);
  }
  if (cur.error) log.appendChild(h('p', 'coach-error', '分析失敗：' + cur.error));
  view.appendChild(log);
}

function applyAnalysisEvent(name, data) {
  var cur = analysis.current;
  if (name === 'start') {
    cur.id = data.id;
  } else if (name === 'tool_use') {
    cur.tools.push({ id: data.id, name: data.name, input: data.input, result: null, isError: false });
  } else if (name === 'tool_result') {
    cur.tools.forEach(function (t) {
      if (t.id === data.id) {
        t.result = data.preview + (data.truncated ? '\n…（僅顯示前段）' : '');
        t.isError = data.is_error;
      }
    });
  } else if (name === 'denied') {
    cur.denied.push(data.tool);
  } else if (name === 'text') {
    cur.text = cur.text ? cur.text + '\n\n' + data.text : data.text;
  } else if (name === 'done') {
    cur.status = data.is_error ? 'error' : 'done';
    cur.report = data.report;
  } else if (name === 'error') {
    cur.status = 'error';
    cur.error = data.message;
  }
  renderAnalysis();
}

/**
 * 外部入口：打開面板並跑一次新的分析（總覽「AI 分析」按鈕）。
 * 不能跑時 reject 一個帶中文說明的 Error，由呼叫端顯示在按鈕旁。
 * @returns {Promise}
 */
function runAnalysis() {
  openCoachPanel();
  if (analysis.running) return Promise.reject(new Error('分析進行中，請等它完成'));
  showAnalysisView(true);

  return api('/api/analysis/status').then(function (status) {
    if (!status.client_allowed) throw new Error('AI 分析僅限本機使用');
    if (!status.enabled) throw new Error('AI 分析無法使用：' + (status.reason || '未知原因'));
    if (status.running) throw new Error('已有一個分析正在進行');

    analysis.running = true;
    analysis.current = {
      id: null, created_at: localIsoNow(), status: 'running',
      tools: [], denied: [], text: '', report: null, error: null
    };
    renderAnalysis();
    return fetch('.' + withAthlete('/api/analysis'), {
      method: 'POST',
      headers: { Accept: 'text/event-stream' }
    });
  }).then(function (res) {
    if (!res.ok) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        throw new Error((data && data.detail) || ('HTTP ' + res.status));
      });
    }
    if (!res.body) throw new Error('這個瀏覽器不支援串流讀取。');
    return pumpSSE(res.body.getReader(), applyAnalysisEvent);
  }).then(function () {
    analysis.running = false;
    if (analysis.current && analysis.current.status === 'running') {
      analysis.current.status = 'error';
      analysis.current.error = '連線中斷，未收到完整結果';
    }
    renderAnalysis();
    return loadAnalysisList();
  }, function (err) {
    analysis.running = false;
    if (analysis.current && analysis.current.status === 'running') {
      analysis.current.status = 'error';
      analysis.current.error = err.message;
    }
    renderAnalysis();
    loadAnalysisList();
    throw err;
  });
}

/** 外部入口：打開面板的分析報告畫面，回看過去的分析（不跑新的）。 */
function showAnalyses() {
  openCoachPanel();
  showAnalysisView(true);
  renderAnalysis();
  return loadAnalysisList().then(function () {
    if (!analysis.current && analysis.reports.length > 0) return openReport(analysis.reports[0].id);
  });
}

// ------------------------------------------------------------ 啟動

function init() {
  if (!$('coach-panel')) return; // 面板不存在（理論上不會發生）就什麼都不做

  api('/api/chat/status').then(function (status) {
    state.status = status;
    if (!status.enabled) {
      renderDisabled('伺服器未設定 ANTHROPIC_API_KEY，AI 教練暫時無法使用。'
        + (status.reason ? '（' + status.reason + '）' : ''));
      return;
    }
    if (!status.client_allowed) {
      renderDisabled('AI 教練僅限本機使用。要讓區網裝置使用，'
        + '請在啟動 server 的環境設定 RC_CHAT_ALLOW_PRIVATE_NETWORK=1。');
      return;
    }
    if (!buildPanel()) return;
    renderChips();
    renderLog();
    loadConversations();
  }).catch(function (err) {
    renderDisabled('無法取得 AI 教練狀態：' + err.message);
  });

  // 切 view 時更新建議問題（路由由 app.js 負責，這裡只跟著換 chips）
  window.addEventListener('hashchange', function () {
    if (state.status && state.status.enabled && state.status.client_allowed) renderChips();
  });
}

// init 由 main.js 在 App 殼就緒後呼叫；runAnalysis／showAnalyses 供總覽「AI 分析」按鈕使用
export {
  init as initCoach,
  runAnalysis,
  showAnalyses,
  renderMarkdown,
  contextLine,
  suggestionsFor
};
