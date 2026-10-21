/**
 * state.js — 共用狀態、路由表、range 常數、標籤對照表與檢視脈絡
 */


export var state = {
  range: '30d',
  athleteId: null,       // 未指定時由後端取資料庫第一位
  sessions: [],
  selectedSessionId: null,
  sessionDetail: null,
  wellness: null,
  trainingDays: [],
  recoveryImpacts: [],
  meta: null,
  today: null,
  loadSeries: null,
  calendarMonth: null,   // 'YYYY-MM'，展開月曆時才需要，預設當月
  calendarData: null,    // /api/calendar 回傳的月曆資料（展開後才載入）
  calendarSelectedDate: null, // 'YYYY-MM-DD'，月曆下方明細顯示的是哪一天
  calendarWeek: null,    // { start, days }：選取日的課表週超出月曆範圍時另抓的那週
  weekDays: null,        // /api/calendar 的本課表週（週一到週日；週條與執行表共用）

  // 今日頁的身體狀況趨勢與最近訓練清單。兩者都取固定區間（近 30 天），
  // 與頂列的 range 切換無關——今日頁不吃 range（見 VIEWS.today.hasRange）。
  todayKpi: null,        // { loadSeries, hrvPoints, rhrPoints, sleepPoints, sleepDurationPoints }
  recentSessions: null,  // /api/sessions 近 30 天的場次（取前 8 筆顯示）
  overview: null,        // 趨勢頁區間摘要用的 /api/overview（吃 range）

  homeOverview: null,    // 首頁用的 /api/overview（固定 30d，只取 VDOT、最佳成績、本週 Zone 2 等不受範圍影響的欄位）
  recentPlans: null,     // 活動 id → 當天對應的課表（最近訓練的「對應課表」欄）

  view: 'home',          // 目前 view：home|calendar|trends|sessions|settings

  // render-on-activate 的髒標記。widthOf() 對 display:none 的容器回 0，
  // 在隱藏的 view 裡畫圖會得到寬度 0 的空 SVG，所以資料回來時若該 view
  // 不在畫面上就只標髒、不畫；切過去時才補畫（見 renderDirtyView）。
  // key 是「區塊」：load 與 wellness 同屬趨勢頁，各自標髒、切到趨勢頁時一起補畫。
  dirty: { overview: false, sessions: false, load: false, wellness: false, calendar: false }
};

// view 的定義：路由 key → 標題、是否吃 range。
// 「吃 range」決定內容頂列的區間切換要不要顯示：趨勢、訓練紀錄顯示，
// 首頁、行事曆與系統設定不受區間影響。
// 2026-10-21 訓練負荷與身體狀況合併為「趨勢」、總覽與今日合併為「首頁」
// （舊網址轉址見 router.js）。
export var VIEWS = {
  home:     { title: '首頁',         hasRange: false },
  calendar: { title: '行事曆',       hasRange: false },
  trends:   { title: '趨勢',         hasRange: true },
  sessions: { title: '訓練紀錄',     hasRange: true },
  settings: { title: '系統設定',     hasRange: false }
};
export var DEFAULT_VIEW = 'home';

// 產品名。分頁標題組成 `<view 標題>｜Running Coach`，<title> 的初始值
// （index.html）是完整的「Running Coach 訓練儀表板」——前者要短，因為
// 瀏覽器分頁很窄，view 標題才是使用者在一排分頁裡真正要分辨的東西。
export var APP_NAME = 'Running Coach';

// range 的合法值改由 /api/meta 的 ranges 動態提供（後端 RANGE_LABELS
// 是唯一真實來源），這裡只留一個防禦性 fallback：若 meta 還沒回來前
// 使用者就想操作（理論上不會發生，因為按鈕本身也是 meta 回來後才產生），
// 或 meta 請求失敗時，仍能辨識這 5 個預設值與 custom: 前綴格式。
export var FALLBACK_RANGES = ['7d', '30d', '90d', '1y', 'all'];
export var CUSTOM_RANGE_PREFIX = 'custom:';
export var CUSTOM_RANGE_PATTERN = /^custom:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/;

export function isValidRangeKey(range) {
  var known = (state.meta && state.meta.ranges)
    ? state.meta.ranges.map(function (r) { return r.key; })
    : FALLBACK_RANGES;
  if (known.indexOf(range) !== -1) return true;
  return CUSTOM_RANGE_PATTERN.test(range);
}

// 身體狀況指標定義改由後端 /api/meta 的 wellness_metric_defs 驅動
// （後端 WELLNESS_METRIC_DEFS 是唯一真實來源：label/unit/decimals/format/
// default_hidden），不再於前端寫死一份。見 wellnessMetricDefs() 與
// FALLBACK_WELLNESS_METRICS（meta 請求失敗時的最小降級路徑）。
export var FALLBACK_WELLNESS_METRICS = [
  { key: 'hrv_ms', label: 'HRV 夜間均值', unit: 'ms', decimals: 1, format: 'number', default_hidden: false },
  { key: 'resting_hr_bpm', label: '靜止心率', unit: 'bpm', decimals: 0, format: 'number', default_hidden: false },
  { key: 'spo2_pct', label: '血氧', unit: '%', decimals: 0, format: 'number', default_hidden: false },
  { key: 'sleep_score', label: '睡眠分數', unit: '', decimals: 0, format: 'number', default_hidden: false },
  { key: 'training_readiness_score', label: '訓練準備度', unit: '', decimals: 0, format: 'number', default_hidden: false },
  { key: 'stress_avg', label: '壓力（睡眠期間）', unit: '', decimals: 0, format: 'number', default_hidden: false },
  { key: 'all_day_stress_avg', label: '壓力（全天）', unit: '', decimals: 0, format: 'number', default_hidden: false },
  { key: 'steps', label: '步數', unit: '步', decimals: 0, format: 'number', default_hidden: false }
];

export function wellnessMetricDefs() {
  return (state.meta && state.meta.wellness_metric_defs) || FALLBACK_WELLNESS_METRICS;
}

export var WORKOUT_TYPE_LABEL = {
  easy: '輕鬆跑',
  tempo: '節奏跑',
  interval: '間歇',
  lsd: '長距離慢跑',
  race: 'Race',
  recovery: '恢復跑',
  unknown: '未分類',
  rest: '休息',
  strength: '重訓課',
  cross_training: '交叉訓練'
};

export var SOURCE_LABEL = { auto: '自動判定', manual: '手動設定' };

// 今日卡／行事曆用：activity_type → 顯示名稱與 CSS 色票 key
// （--activity-* 變數，見 styles.css；未列出的類型退回 activity-other）。
export var ACTIVITY_TYPE_LABEL = {
  running: '跑步',
  treadmill_running: '跑步機',
  track_running: '田徑場跑步',
  trail_running: '越野跑',
  strength_training: '重訓',
  walking: '健走',
  cycling: '騎車',
  hiking: '登山'
};
export var ACTIVITY_COLOR_VARS = {
  running: '--activity-running',
  treadmill_running: '--activity-running',
  track_running: '--activity-running',
  trail_running: '--activity-running',
  strength_training: '--activity-strength_training',
  walking: '--activity-walking',
  cycling: '--activity-cycling'
};
export function activityColorVar(activityType) {
  return ACTIVITY_COLOR_VARS[activityType] || '--activity-other';
}

export var DAY_STATE_LABEL = {
  rest_ok: '休息',
  planned_done: '完成',
  planned_missed: '未執行',
  today_pending: '今天待完成',
  unplanned_activity: '額外訓練',
  future: '尚未發生'
};

export var CONFIDENCE_LABEL = { high: '可信', low: '低信賴', unusable: '不可用' };

// Garmin（Firstbeat）的 HRV 狀態分類。值來自 daily_wellness.hrv_status，
// 由手錶端演算法判定，本專案只做顯示與標注偏誤，不重新計算也不覆寫。
// Garmin 訓練狀態：phrase 形如 PRODUCTIVE_3，底線後的數字是回饋文案變體，
// 只取前綴對照
export var GARMIN_TRAINING_STATUS_LABEL = {
  PEAKING: '巔峰',
  PRODUCTIVE: '有效訓練',
  MAINTAINING: '維持',
  RECOVERY: '恢復',
  UNPRODUCTIVE: '效率低落',
  DETRAINING: '退化',
  OVERREACHING: '過度訓練',
  STRAINED: '疲勞'
};
export var GARMIN_ACWR_STATUS_LABEL = { LOW: '偏低', OPTIMAL: '最佳', HIGH: '偏高', VERY_HIGH: '非常高' };

export var HRV_STATUS_LABEL = {
  BALANCED: '平衡',
  UNBALANCED: '不平衡',
  LOW: '偏低',
  POOR: '不佳',
  NOT_ENOUGH_DATA: '資料不足'
};
// 只有「平衡」是預期狀態，其餘都值得注意——用 CSS class 區分顏色，
// 但不在這裡寫任何建議文字（判讀屬 AI 教練，見頁尾聲明）。
export var HRV_STATUS_CLASS = {
  BALANCED: 'hrv-status-balanced',
  UNBALANCED: 'hrv-status-alert',
  LOW: 'hrv-status-alert',
  POOR: 'hrv-status-alert',
  NOT_ENOUGH_DATA: 'hrv-status-muted'
};

/**
 * 目前的檢視脈絡，供 AI 教練面板產生「問這一頁」的建議問題、並在送出時
 * 附上〔檢視脈絡〕行。
 *
 * 刻意只回「使用者在看什麼」，不回任何數據——面板不在前端拼數據餵
 * 模型，模型自己呼叫 MCP 工具拿含信賴度的事實（D.1 §7.3 三欄位契約）。
 * @returns {{view:string, range:string, sessionId:?number, month:?string, date:?string}}
 */
export function getViewContext() {
  return {
    view: state.view,
    range: state.range,
    sessionId: state.view === 'sessions' ? state.selectedSessionId : null,
    month: state.view === 'calendar' ? state.calendarMonth : null,
    date: state.today ? state.today.date : null
  };
}
