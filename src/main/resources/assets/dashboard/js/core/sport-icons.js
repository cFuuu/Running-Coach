/**
 * sport-icons.js — 運動種類：activity_type → 種類 key、圖示 SVG、中文名稱（全站唯一來源）
 *
 * 種類 key（run／strength／walk／hike／bike／swim／race／other）同時是：
 *   - SPORT_ICONS 的 key（E.1 定案的八個雙色調圖示，以 currentColor 上色）
 *   - styles.css 色彩 token 的名稱片段：--sport-<key>-bg／--sport-<key>-fg
 *   - .sport-chip 修飾 class：.sport-chip--<key>
 *
 * 顏色只代表運動種類，不代表課種（DASHBOARD_TASKS E.1）。
 * 跑步家族沿用 activity_types.RUNNING_ACTIVITY_TYPES（後端唯一來源），
 * 這裡的清單需與它保持一致。
 */

// 最終 SVG：直接搬自 DASHBOARD_TASKS.md E.1，不另畫一份。
export var SPORT_ICONS = {
  run: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="3.1"><circle cx="16" cy="3.9" r="2.4" fill="currentColor" stroke="none"/><path d="M13.6 8.1 10 8.7 8.1 11.3M11.2 12.6 8.8 16.6 4.7 17.5" stroke-opacity=".4"/><path d="M14.2 7.3 11.2 12.6l3.4 2.8-1.2 5.4M13.9 8l2.9 2.7 2.8-.5"/></svg>',
  strength: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4.6" y="6.5" width="3.2" height="11" rx="1.2" fill="currentColor" fill-opacity=".22"/><rect x="16.2" y="6.5" width="3.2" height="11" rx="1.2" fill="currentColor" fill-opacity=".22"/><path d="M2.6 9.5v5M21.4 9.5v5M7.8 12h8.4"/></svg>',
  walk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="3.1"><circle cx="12.8" cy="3.9" r="2.4" fill="currentColor" stroke="none"/><path d="M12.3 8.2 10 11.2l-.6 2.6M11.6 13.2 10 17.1l-1.2 3.9" stroke-opacity=".4"/><path d="M12.3 7.5 11.6 13.2l2 3.8 1 4M12.4 8.3l2 3 1.8 1.4"/></svg>',
  hike: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 19.5 8.6 9.6l3.3 5.1 2.6-3.6 7 8.4z" fill="currentColor" fill-opacity=".22"/><path d="M7.1 12.1 8.6 13.2l1.4-1.1"/><circle cx="17.5" cy="5" r="1.8" fill="currentColor"/></svg>',
  bike: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="17.6" r="3.8" stroke-width="1.8" fill="currentColor" fill-opacity=".22"/><circle cx="19" cy="17.6" r="3.8" stroke-width="1.8" fill="currentColor" fill-opacity=".22"/><path stroke-width="1.6" stroke-opacity=".4" d="M5 17.6h5.8l6.4-6.4 1.8 6.4M10.8 17.6 8.2 10.4"/><circle cx="18" cy="5.4" r="2.3" fill="currentColor" stroke="none"/><path stroke-width="3.1" d="M8.2 9.8 14.4 7.8l2.8 3.6M8.2 9.8l4.6 2.6-2 5.2"/></svg>',
  swim: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path stroke-width="2.9" d="M3.4 13.4 12.6 12.4"/><circle cx="16.6" cy="12.2" r="2" fill="currentColor" stroke="none"/><path stroke-width="2.9" d="M11.2 12.2Q12.4 5 18.6 7.2"/><path stroke-width="1.8"  d="M2 16.8c1.7 0 1.7-1.3 3.3-1.3S7 16.8 8.6 16.8s1.7-1.3 3.4-1.3 1.7 1.3 3.4 1.3 1.6-1.3 3.3-1.3 1.7 1.3 3.3 1.3"/><path stroke-width="1.8" stroke-opacity=".4" d="M2 20.4c1.7 0 1.7-1.3 3.3-1.3S7 20.4 8.6 20.4s1.7-1.3 3.4-1.3 1.7 1.3 3.4 1.3 1.6-1.3 3.3-1.3 1.7 1.3 3.3 1.3"/></svg>',
  race: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8.2 2.8 10.6 9M15.8 2.8 13.4 9"/><circle cx="12" cy="14.8" r="5.6" fill="currentColor" fill-opacity=".22"/><path d="m12 12.2.9 1.8 2 .3-1.4 1.4.3 2-1.8-.9-1.8.9.3-2-1.4-1.4 2-.3z" fill="currentColor" stroke-width="1"/></svg>',
  other: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 12.5h3.8l2.3-5.5 4 11 2.4-5.5h6.5"/></svg>'
};

export var SPORT_KEYS = ['run', 'strength', 'walk', 'hike', 'bike', 'swim', 'race', 'other'];

export var SPORT_LABEL = {
  run: '跑步',
  strength: '健身',
  walk: '健走',
  hike: '登山',
  bike: '騎乘',
  swim: '游泳',
  race: '賽事',
  other: '其他'
};

// 明確列出的 activity_type。跑步家族 = activity_types.RUNNING_ACTIVITY_TYPES。
var SPORT_BY_ACTIVITY_TYPE = {
  running: 'run',
  treadmill_running: 'run',
  track_running: 'run',
  trail_running: 'run',
  strength_training: 'strength',
  walking: 'walk',
  hiking: 'hike',
  lap_swimming: 'swim',
  open_water_swimming: 'swim',
  swimming: 'swim'
};

// 自行車在 Garmin 有很多子類型（road_biking、indoor_cycling、mountain_biking、
// gravel_cycling、virtual_ride、e_bike_*…），逐一列舉一定會漏，改用字樣比對。
// 排除 motorcycling（機車，不是自行車）。
var BIKE_PATTERN = /^(?!motor)(?:.*(?:cycling|biking|bike)|virtual_ride)/;

/**
 * activity_type（＋選填的 workout_type）→ 運動種類 key。
 * workout_type 為 'race' 時一律視為賽事（優先於 activity_type）。
 * @param {string|null|undefined} activityType
 * @param {string|null|undefined} [workoutType]
 * @returns {string} SPORT_KEYS 之一
 */
export function sportOf(activityType, workoutType) {
  if (workoutType === 'race') return 'race';
  var t = String(activityType || '').toLowerCase();
  if (SPORT_BY_ACTIVITY_TYPE[t]) return SPORT_BY_ACTIVITY_TYPE[t];
  if (BIKE_PATTERN.test(t)) return 'bike';
  return 'other';
}

/**
 * 建一個圖示節點（<span class="sport-icon">內含 SVG）。SVG 為本檔的常數字串，
 * 不含任何外部輸入，用 innerHTML 是安全的。
 * @param {string} sport SPORT_KEYS 之一（未知 key 退回 other）
 * @returns {HTMLElement}
 */
export function sportIconNode(sport) {
  var span = document.createElement('span');
  span.className = 'sport-icon';
  span.setAttribute('aria-hidden', 'true');
  span.innerHTML = SPORT_ICONS[sport] || SPORT_ICONS.other;
  return span;
}
