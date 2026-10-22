/**
 * mock-data.js — 開發用「虛構」假資料（DEV ONLY）
 *
 * ⚠️ 這裡的所有數字都是憑空編造的，與任何真實使用者資料無關，
 *    也刻意不含任何個人識別資訊（email／姓名／Garmin ID）。
 *
 * 用途：Task A 後端尚未完成前，讓前端可獨立開發與驗證。
 * 資料結構完全依照 docs/dev/DASHBOARD_TASKS.md 的 API Contract。
 *
 * 【整合時如何關閉假資料】
 *   方式一（推薦）：直接把 index.html 中這支 <script> 標籤整行刪除。
 *                   app.js 偵測不到 window.MOCK_API 就會自動改打真實 API。
 *   方式二：在網址列加上 ?mock=0（例如 http://127.0.0.1:8000/?mock=0），
 *           可在不改檔案的情況下強制走真實 API。
 *   方式三：加上 ?mock=1 可在真實後端存在時強制回到假資料（除錯用）。
 *
 * 假資料刻意涵蓋以下邊界狀態，供 UI 驗證：
 *   - hr_zones / laps / records / hr_drift 的 available:false
 *   - recovery-impact 的 next_day.available:false
 *   - wellness metric 的 clipped:true
 *   - points 中缺值日期直接不出現（前端應斷線，不補 0）
 *   - workout_type 為 null 的活動
 */
(function () {
  'use strict';

  // ---- 小工具：產生日期字串（相對於固定基準日，讓假資料可重現）----
  var BASE_DATE = '2026-08-16'; // 假想的「今天」

  function toISODate(d) {
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1).padStart(2, '0');
    var day = String(d.getUTCDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  function dateMinus(days) {
    var d = new Date(BASE_DATE + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - days);
    return toISODate(d);
  }

  // 決定性的偽亂數（同樣的 seed 永遠給同樣的序列，方便重現畫面）
  function makeRandom(seed) {
    var state = seed >>> 0;
    return function () {
      state = (state * 1664525 + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }

  var RANGE_DAYS = { '7d': 7, '30d': 30, '90d': 90, '1y': 365, 'all': 2500 };
  var CUSTOM_RANGE_PATTERN = /^custom:(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$/;

  /**
   * 把 range 參數換算成 {start, end} 日期字串。支援 custom:YYYY-MM-DD:YYYY-MM-DD
   * ——與後端 dashboard_queries.resolve_range() 的兩種路徑對應，讓假資料模式
   * 也能驗證自訂區間（否則 Phase 4 的功能在 mock 模式下永遠測不到）。
   * 4 個原本各自重複 `var days = RANGE_DAYS[range] || 30` 的函式收斂成呼叫這裡。
   */
  function resolveWindow(range) {
    var custom = CUSTOM_RANGE_PATTERN.exec(range);
    if (custom) return { start: custom[1], end: custom[2] };
    var days = RANGE_DAYS[range] || 30;
    return { start: dateMinus(days - 1), end: BASE_DATE };
  }

  // ---- 假的活動清單（涵蓋各種 workout_type 與缺值狀況）----
  var WORKOUT_TYPES = ['easy', 'tempo', 'interval', 'lsd', 'race', 'recovery', 'unknown', null];

  function buildSessions() {
    var rnd = makeRandom(20260817);
    var sessions = [];
    var id = 900;
    // 產生近 400 天內、平均每 2~3 天一場的假活動
    for (var dayOffset = 0; dayOffset < 400; dayOffset += 1) {
      if (rnd() > 0.42) continue;
      var wt = WORKOUT_TYPES[Math.floor(rnd() * WORKOUT_TYPES.length)];
      var distance = Math.round((3 + rnd() * 16) * 100) / 100;
      var pace = Math.round(300 + rnd() * 150); // 5:00 ~ 7:30 /km
      var duration = Math.round(distance * pace);
      var avgHr = Math.round(132 + rnd() * 28);
      var date = dateMinus(dayOffset);
      var hour = 6 + Math.floor(rnd() * 13);
      sessions.push({
        id: id,
        started_at: date + 'T' + String(hour).padStart(2, '0') + ':' + (dayOffset % 6 === 0 ? '05' : '43') + ':31',
        date: date,
        title: hour < 11 ? '早晨跑步' : (hour < 17 ? '午後跑步' : '傍晚跑步'),
        activity_type: 'running',
        workout_type: wt,
        workout_type_source: wt === null ? null : (dayOffset % 9 === 0 ? 'manual' : 'auto'),
        distance_km: distance,
        duration_sec: duration,
        avg_pace_sec_per_km: pace,
        // 刻意讓部分舊資料缺值，驗證前端容錯
        avg_hr_bpm: dayOffset % 11 === 5 ? null : avgHr,
        max_hr_bpm: dayOffset % 11 === 5 ? null : avgHr + Math.round(8 + rnd() * 20),
        avg_cadence_spm: dayOffset % 7 === 3 ? null : Math.round(162 + rnd() * 14),
        aerobic_te: dayOffset % 13 === 4 ? null : Math.round((2 + rnd() * 3) * 10) / 10
      });
      id -= 1;
    }
    return sessions; // 已是由新到舊（dayOffset 遞增 = 日期遞減）
  }

  var ALL_SESSIONS = buildSessions();

  // ---- 單場詳細資料 ----
  function buildLaps(session, seed) {
    var rnd = makeRandom(seed);
    var laps = [];
    var full = Math.floor(session.distance_km);
    for (var i = 1; i <= full; i += 1) {
      var pace = session.avg_pace_sec_per_km + Math.round((rnd() - 0.45) * 60);
      laps.push({
        lap: i,
        distance_km: 1.0,
        duration_sec: Math.round(pace * 10) / 10,
        pace_sec_per_km: pace,
        avg_hr_bpm: session.avg_hr_bpm === null ? null : session.avg_hr_bpm + Math.round((i / Math.max(full, 1)) * 12 - 4)
      });
    }
    var remainder = Math.round((session.distance_km - full) * 100) / 100;
    if (remainder >= 0.05) {
      var lastPace = session.avg_pace_sec_per_km + Math.round((rnd() - 0.4) * 50);
      laps.push({
        lap: full + 1,
        distance_km: remainder,
        duration_sec: Math.round(lastPace * remainder * 10) / 10,
        pace_sec_per_km: lastPace,
        avg_hr_bpm: session.avg_hr_bpm === null ? null : session.avg_hr_bpm + 8
      });
    }
    return laps;
  }

  function buildRecords(session, seed) {
    var rnd = makeRandom(seed);
    var points = [];
    var every = 10;
    var total = session.duration_sec;
    var dist = 0;
    // 用「隨機漫步 + 回歸均值」產生較接近真實 GPS 手錶的平滑曲線，
    // 而不是每點獨立亂數（那會畫成一團鋸齒，看不出趨勢）
    var paceOffset = 0;
    var hrOffset = 0;
    for (var t = 0; t <= total; t += every) {
      var progress = t / Math.max(total, 1);
      paceOffset = paceOffset * 0.82 + (rnd() - 0.5) * 12;
      hrOffset = hrOffset * 0.88 + (rnd() - 0.5) * 3;
      var pace = t === 0 ? null : Math.round(session.avg_pace_sec_per_km + paceOffset);
      // 刻意在中段製造缺值（null），驗證前端「斷線而非補 0」
      var hrMissing = progress > 0.42 && progress < 0.47;
      var hr = session.avg_hr_bpm === null || hrMissing
        ? null
        // 起跑後心率逐步上升（心率漂移），再疊上平滑的小幅波動
        : Math.round(session.avg_hr_bpm - 14 + progress * 22 + hrOffset);
      if (t > 0) dist += every / (pace || session.avg_pace_sec_per_km);
      points.push({
        elapsed_sec: t,
        distance_km: Math.round(dist * 1000) / 1000,
        hr_bpm: hr,
        pace_sec_per_km: pace,
        cadence_spm: t === 0 ? 0 : Math.round(168 + hrOffset * 0.6)
      });
    }
    return { available: true, sample_every_sec: every, points: points };
  }

  /**
   * 模擬與後端 dashboard_queries._build_hr_zones() 相同的規則：
   * zone 0（低於 Z1 的暖身）獨立回傳；zones 固定回傳 Z1~Z5 共 5 筆
   * （不因某場強度低就少幾列，方便跨場次比較時圖表軸線一致）；
   * zone 6 是裝置固定 padding（真實資料恆為 0），不計入 5 列也不計入分母；
   * pct 分母含 zone 0。假資料結構必須與真實 API 回傳形狀一致，
   * 否則 mock 模式測不到這段邏輯。
   */
  function buildHrZones(session, seed) {
    var rnd = makeRandom(seed);
    var secondsByZone = {};
    var remain = session.duration_sec;
    for (var z = 0; z <= 5; z += 1) {
      var share = z === 0 ? 0.04 : 0.12 + rnd() * 0.2;
      var sec = Math.round(session.duration_sec * share);
      if (sec > remain) sec = Math.max(remain, 0);
      remain -= sec;
      secondsByZone[z] = sec;
    }
    // zone 6：裝置固定 padding，真實資料恆為 0，不參與 total

    var total = 0;
    for (var i = 0; i <= 5; i += 1) total += secondsByZone[i];

    function withPct(zone) {
      var sec = secondsByZone[zone] || 0;
      return { zone: zone, seconds: sec, pct: total > 0 ? (sec / total) * 100 : 0 };
    }

    var zones = [];
    for (var zi = 1; zi <= 5; zi += 1) zones.push(withPct(zi));

    return {
      available: true,
      zones: zones,
      below_zone_1: withPct(0),
      total_seconds: total
    };
  }

  function buildSessionDetail(id) {
    var session = null;
    for (var i = 0; i < ALL_SESSIONS.length; i += 1) {
      if (ALL_SESSIONS[i].id === id) { session = ALL_SESSIONS[i]; break; }
    }
    if (!session) return null;

    var seed = id * 7919;
    var rnd = makeRandom(seed);

    // 依 id 決定要示範哪些 available:false 情境（每 5 場出現一次缺資料）
    var noFit = id % 5 === 0;          // 無 FIT → laps/records/hr_drift 皆無
    var noZones = id % 7 === 0;        // 無心率區間

    var detail = {
      id: session.id,
      started_at: session.started_at,
      title: session.title,
      activity_type: session.activity_type,
      workout_type: session.workout_type,
      workout_type_source: session.workout_type_source,
      planned: id % 3 === 0
        ? { workout_type: 'lsd', planned_distance_km: Math.round((session.distance_km + 2) * 10) / 10 }
        : null,
      summary: {
        distance_km: session.distance_km,
        duration_sec: session.duration_sec,
        avg_pace_sec_per_km: session.avg_pace_sec_per_km,
        avg_hr_bpm: session.avg_hr_bpm,
        max_hr_bpm: session.max_hr_bpm,
        avg_cadence_spm: session.avg_cadence_spm,
        aerobic_te: session.aerobic_te,
        elevation_gain_m: Math.round(rnd() * 120),
        calories: Math.round(session.distance_km * 62)
      },
      hr_zones: noZones
        ? { available: false, reason: '此活動無心率區間資料' }
        : buildHrZones(session, seed + 1),
      laps: noFit
        ? { available: false, reason: '此活動無分圈資料' }
        : { available: true, source: 'fit', laps: buildLaps(session, seed + 2) },
      records: noFit
        ? { available: false, reason: '此活動尚未解析 FIT 逐秒資料' }
        : buildRecords(session, seed + 3),
      hr_drift: { available: false, reason: '需要逐秒資料才能計算心率漂移' }
    };

    if (!noFit && session.avg_hr_bpm !== null) {
      var first = session.avg_hr_bpm - 5;
      var second = session.avg_hr_bpm + Math.round(rnd() * 12);
      detail.hr_drift = {
        available: true,
        first_half_avg_hr: first,
        second_half_avg_hr: second,
        drift_pct: Math.round(((second - first) / first) * 1000) / 10
      };
    }
    return detail;
  }

  // ---- 每日身體狀況（十三指標；契約排除的電池/皮膚溫度類指標一律不產生）----
  // coverage 刻意設定成不同起日，讓 range=1y/all 時部分指標 clipped:true
  var METRIC_DEFS = [
    { key: 'hrv_ms', base: 46, spread: 9, decimals: 1, coverageDaysAgo: 300, gapMod: 9 },
    { key: 'resting_hr_bpm', base: 49, spread: 5, decimals: 0, coverageDaysAgo: 900, gapMod: 13 },
    { key: 'spo2_pct', base: 96, spread: 2, decimals: 0, coverageDaysAgo: 420, gapMod: 7 },
    { key: 'sleep_score', base: 76, spread: 14, decimals: 0, coverageDaysAgo: 900, gapMod: 11 },
    { key: 'training_readiness_score', base: 64, spread: 18, decimals: 0, coverageDaysAgo: 260, gapMod: 8 },
    { key: 'stress_avg', base: 28, spread: 10, decimals: 0, coverageDaysAgo: 900, gapMod: 12 },
    { key: 'all_day_stress_avg', base: 38, spread: 12, decimals: 0, coverageDaysAgo: 180, gapMod: 10 },
    { key: 'steps', base: 9200, spread: 4200, decimals: 0, coverageDaysAgo: 2100, gapMod: 17 },
    // Phase 5 新開放的 5 個指標
    { key: 'sleep_duration_sec', base: 25200, spread: 3600, decimals: 0, coverageDaysAgo: 900, gapMod: 11 },
    { key: 'hrv_weekly_avg_ms', base: 46, spread: 6, decimals: 1, coverageDaysAgo: 900, gapMod: 8 },
    { key: 'recovery_time_hours', base: 20, spread: 10, decimals: 1, coverageDaysAgo: 900, gapMod: 9 },
    { key: 'acwr', base: 1.0, spread: 0.4, decimals: 2, coverageDaysAgo: 900, gapMod: 10 },
    { key: 'respiration_rate', base: 15, spread: 2, decimals: 1, coverageDaysAgo: 300, gapMod: 9 }
  ];

  // 對照後端 dashboard_queries.WELLNESS_METRIC_DEFS，供 /api/meta 假資料
  // 動態組出 wellness_metric_defs，避免這裡與 METRIC_DEFS 重複維護一份
  // 又和後端結構脫節。
  var WELLNESS_METRIC_LABELS = {
    hrv_ms: { label: 'HRV 夜間均值', unit: 'ms', format: 'number' },
    resting_hr_bpm: { label: '靜止心率', unit: 'bpm', format: 'number' },
    spo2_pct: { label: '血氧', unit: '%', format: 'number' },
    sleep_score: { label: '睡眠分數', unit: '', format: 'number' },
    training_readiness_score: { label: '訓練準備度', unit: '', format: 'number' },
    stress_avg: { label: '壓力（睡眠期間）', unit: '', format: 'number' },
    all_day_stress_avg: { label: '壓力（全天）', unit: '', format: 'number' },
    steps: { label: '步數', unit: '步', format: 'number' },
    sleep_duration_sec: { label: '睡眠時長', unit: '', format: 'duration' },
    hrv_weekly_avg_ms: { label: 'HRV 週均', unit: 'ms', format: 'number' },
    recovery_time_hours: { label: '恢復時間', unit: 'hr', format: 'number' },
    acwr: { label: '急慢性負荷比（ACWR）', unit: '', format: 'number' },
    respiration_rate: { label: '呼吸率', unit: '/min', format: 'number' }
  };
  var WELLNESS_METRICS_DEFAULT_HIDDEN = ['respiration_rate'];

  function buildWellness(range) {
    var window = resolveWindow(range);
    var startMs = Date.parse(window.start + 'T00:00:00Z');
    var endMs = Date.parse(window.end + 'T00:00:00Z');
    var totalDays = Math.round((endMs - startMs) / 86400000) + 1;
    var metrics = {};
    METRIC_DEFS.forEach(function (def, idx) {
      var rnd = makeRandom(1000 + idx * 37);
      var earliest = dateMinus(def.coverageDaysAgo);
      var points = [];
      for (var i = 0; i < totalDays; i += 1) {
        var ms = startMs + i * 86400000;
        var date = new Date(ms).toISOString().slice(0, 10);
        var d = Math.round((Date.parse(BASE_DATE + 'T00:00:00Z') - ms) / 86400000);
        if (date < earliest) continue;              // 早於 coverage 的日期完全沒有點
        if (((d % def.gapMod) + def.gapMod) % def.gapMod === 3) continue; // 刻意缺值 → 前端應斷線，不補 0
        var v = def.base + (rnd() - 0.5) * 2 * def.spread + Math.sin(d / 11) * def.spread * 0.35;
        var value = def.decimals === 1 ? Math.round(v * 10) / 10 : Math.round(v);
        points.push({ date: date, value: value });
      }
      points.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
      metrics[def.key] = {
        available: points.length > 0,
        clipped: window.start < earliest,
        coverage: { earliest_date: earliest, latest_date: BASE_DATE },
        points: points
      };
      if (points.length === 0) {
        metrics[def.key].reason = '此區間無資料';
      }
    });
    return {
      range: range,
      start_date: window.start,
      end_date: window.end,
      metrics: metrics
    };
  }

  // ---- 訓練日 ----
  function buildTrainingDays(range) {
    var window = resolveWindow(range);
    var out = [];
    ALL_SESSIONS.forEach(function (s) {
      if (s.date >= window.start && s.date <= window.end && out.indexOf(s.date) === -1) out.push(s.date);
    });
    out.sort();
    return { range: range, training_days: out };
  }

  // ---- 恢復影響 ----
  function buildRecoveryImpact(range) {
    var window = resolveWindow(range);
    var impacts = [];
    ALL_SESSIONS.forEach(function (s) {
      if (s.date < window.start || s.date > window.end) return;
      var rnd = makeRandom(s.id * 31);
      var nextDate = new Date(s.date + 'T00:00:00Z');
      nextDate.setUTCDate(nextDate.getUTCDate() + 1);
      var nextDay;
      if (s.id % 6 === 0) {
        nextDay = { date: toISODate(nextDate), available: false, reason: '隔天沒有身體狀況資料' };
      } else {
        nextDay = {
          date: toISODate(nextDate),
          available: true,
          hrv_delta: Math.round((rnd() - 0.55) * 12 * 10) / 10,
          hrv_delta_pct: Math.round((rnd() - 0.55) * 24 * 10) / 10,
          resting_hr_delta: Math.round((rnd() - 0.4) * 6),
          training_readiness_delta: Math.round((rnd() - 0.55) * 26)
        };
      }
      impacts.push({
        activity_id: s.id,
        training_date: s.date,
        workout_type: s.workout_type,
        distance_km: s.distance_km,
        next_day: nextDay
      });
    });
    return { range: range, impacts: impacts };
  }

  // ---- 今日卡 ----
  // BASE_DATE 是假想的「今天」，剛好落在 ALL_SESSIONS 資料範圍內，可直接
  // 從假資料反查「最近一場」與「今日/明日課表」，不需要另建一份平行資料。
  function buildToday() {
    var rnd = makeRandom(20260900);
    var lastActivity = ALL_SESSIONS.length > 0 ? ALL_SESSIONS[0] : null;

    function fact(value, source, level, biasDetail) {
      return {
        value: value,
        source: source,
        confidence: { level: level, biases: biasDetail ? [{ code: 'mock_bias', level: level, detail: biasDetail }] : [] }
      };
    }

    return {
      date: BASE_DATE,
      freshness: { wellness_latest: BASE_DATE, activity_latest: lastActivity ? lastActivity.date : null, days_behind: 0 },
      races: {
        next: { name: '範例路跑（假資料）', distance: '10K', distance_km: 10, date: dateMinus(-25), days_left: 25, is_target: false },
        target: { name: '範例馬拉松（假資料）', distance: '42K', distance_km: 42, date: dateMinus(-60), days_left: 60, is_target: true },
        all: [
          { name: '範例路跑（假資料）', distance: '10K', distance_km: 10, date: dateMinus(-25), days_left: 25, is_target: false },
          { name: '範例半馬（假資料）', distance: '21K', distance_km: 21, date: dateMinus(-42), days_left: 42, is_target: false },
          { name: '範例馬拉松（假資料）', distance: '42K', distance_km: 42, date: dateMinus(-60), days_left: 60, is_target: true }
        ]
      },
      planned: {
        today: { workout_type: 'easy', planned_distance_km: 6.0, planned_pace_sec_per_km: 340, notes: null, plan_source: 'generated', created_at: dateMinus(20) + 'T00:00:00' },
        tomorrow: { workout_type: 'rest', planned_distance_km: 0, planned_pace_sec_per_km: null, notes: null, plan_source: 'generated', created_at: dateMinus(20) + 'T00:00:00' }
      },
      readiness: {
        today: {
          date: BASE_DATE,
          readiness: 'normal',
          triggers: [],
          dimensions_evaluated: ['consecutive_training_days', 'tsb', 'hrv'],
          not_assessed: []
        },
        latest_evaluable: null
      },
      load: {
        atl: fact(Math.round(60 + rnd() * 20), 'Banister EWMA（7 日時間常數）', 'low', 'HRmax 為手錶顯示值、非實測（假資料示範）'),
        ctl: fact(Math.round(55 + rnd() * 15), 'Banister EWMA（42 日時間常數）', 'low', 'HRmax 為手錶顯示值、非實測（假資料示範）'),
        tsb: fact(Math.round((rnd() - 0.5) * 20), 'TSB = CTL − ATL', 'low', 'TSB 門檻為工程實務決策、無文獻背書（假資料示範）')
      },
      hrv: {
        today_ms: fact(Math.round(45 + rnd() * 10), 'daily_wellness.hrv_ms', 'high'),
        weekly_avg_ms: fact(Math.round(46 + rnd() * 8), 'daily_wellness.hrv_weekly_avg_ms（7 日滾動均值）', 'high'),
        last_night_5min_high_ms: fact(Math.round(80 + rnd() * 25), 'daily_wellness.hrv_last_night_5min_high（昨夜最高 5 分鐘平均）', 'high'),
        delta_pct: fact(Math.round((rnd() - 0.5) * 16 * 10) / 10, '(今日 - 7日均值) / 7日均值', 'low', 'HRV 短期波動百分比形式尚未經驗證（假資料示範）')
      },
      consecutive_training_days: { value: 2, threshold: 6, threshold_source: 'default' },
      last_activity: lastActivity ? {
        id: lastActivity.id, date: lastActivity.date, activity_type: lastActivity.activity_type,
        workout_type: lastActivity.workout_type, distance_km: lastActivity.distance_km,
        avg_pace_sec_per_km: lastActivity.avg_pace_sec_per_km, avg_hr_bpm: lastActivity.avg_hr_bpm
      } : null
    };
  }

  // ---- 行事曆／週條 ----
  // 用 ALL_SESSIONS 反推每日活動，課表用簡化的固定週期樣板（週一/三/五
  // easy、週六 lsd，其餘 rest），足以示範 day_state 各種分類。
  var CALENDAR_PLAN_CYCLE = { 1: 'easy', 3: 'tempo', 5: 'easy', 6: 'lsd' }; // 0=週日
  function plannedWorkoutTypeFor(dateStr) {
    var dow = new Date(dateStr + 'T00:00:00Z').getUTCDay();
    return CALENDAR_PLAN_CYCLE[dow] || 'rest';
  }

  function buildCalendarDay(dateStr) {
    var sessionsThatDay = ALL_SESSIONS.filter(function (s) { return s.date === dateStr; });
    var workoutType = plannedWorkoutTypeFor(dateStr);
    var planned = workoutType === 'rest' ? [] : [{
      id: dateStr, workout_type: workoutType, planned_distance_km: workoutType === 'lsd' ? 12 : 6,
      planned_pace_sec_per_km: 340, plan_source: 'generated'
    }];
    var activities = sessionsThatDay.map(function (s) {
      return {
        id: s.id, activity_type: s.activity_type, is_running: true,
        workout_type: s.workout_type, distance_km: s.distance_km, duration_sec: s.duration_sec,
        title: s.title,
        plan_match: planned.length > 0
          ? { state: 'inferred', method: 'date_and_sport', reason: null }
          : { state: 'none', method: null, reason: 'no_compatible_plan' }
      };
    });
    var hasRunningPlan = workoutType !== 'rest';
    var anyMatched = activities.some(function (a) { return a.plan_match.state === 'inferred' || a.plan_match.state === 'exact'; });
    var isToday = dateStr === BASE_DATE;
    var isFuture = dateStr > BASE_DATE;
    var dayState;
    if (isFuture) dayState = 'future';
    else if (hasRunningPlan && anyMatched) dayState = 'planned_done';
    else if (hasRunningPlan && isToday) dayState = 'today_pending';
    else if (hasRunningPlan) dayState = 'planned_missed';
    else if (activities.length > 0) dayState = 'unplanned_activity';
    else dayState = 'rest_ok';
    return { date: dateStr, planned: planned, activities: activities, day_state: dayState };
  }

  function buildCalendar(start, end) {
    var days = [];
    var cursor = start;
    while (cursor <= end) {
      days.push(buildCalendarDay(cursor));
      var d = new Date(cursor + 'T00:00:00Z');
      d.setUTCDate(d.getUTCDate() + 1);
      cursor = toISODate(d);
    }
    return { start: start, end: end, today: BASE_DATE, days: days };
  }

  // ---- 訓練負荷 ----
  function buildTrainingLoad(range) {
    var window = resolveWindow(range);
    var rnd = makeRandom(20260901);
    var startMs = Date.parse(window.start + 'T00:00:00Z');
    var endMs = Date.parse(window.end + 'T00:00:00Z');
    var totalDays = Math.round((endMs - startMs) / 86400000) + 1;
    var atl = 60, ctl = 55;
    var series = [];
    for (var i = 0; i < totalDays; i += 1) {
      var date = toISODate(new Date(startMs + i * 86400000));
      atl = atl * 0.86 + (30 + rnd() * 40) * 0.14;
      ctl = ctl * 0.976 + (30 + rnd() * 40) * 0.024;
      var tsb = Math.round((ctl - atl) * 10) / 10;
      series.push({
        date: date,
        atl: { value: Math.round(atl * 10) / 10, source: 'Banister EWMA（7 日時間常數）', confidence: { level: 'low', biases: [] } },
        ctl: { value: Math.round(ctl * 10) / 10, source: 'Banister EWMA（42 日時間常數）', confidence: { level: 'low', biases: [] } },
        tsb: { value: tsb, source: 'TSB = CTL − ATL', confidence: { level: 'low', biases: [] } },
        uncertain: false
      });
    }
    return { range: range, start_date: window.start, end_date: window.end, series: series };
  }

  function filterSessions(range) {
    var window = resolveWindow(range);
    var list = ALL_SESSIONS.filter(function (s) { return s.date >= window.start && s.date <= window.end; });
    return {
      range: range,
      start_date: window.start,
      end_date: window.end,
      sessions: list
    };
  }

  var META = {
    athlete: { id: 1, name: '示範跑者' }, // 虛構名稱，非真實使用者
    metric_coverage: {
      hrv_ms: { earliest_date: dateMinus(300), latest_date: BASE_DATE },
      activities: { earliest_date: dateMinus(2200), latest_date: BASE_DATE }
    },
    // 與後端 dashboard_queries.RANGE_LABELS 一致，供 app.js 動態產生按鈕；
    // mock 模式下也要提供，否則假資料模式測不到「後端驅動按鈕」這段邏輯。
    ranges: [
      { key: '7d', label: '7 天' },
      { key: '30d', label: '30 天' },
      { key: '90d', label: '90 天' },
      { key: '1y', label: '1 年' },
      { key: 'all', label: '全部' }
    ],
    data_bounds: { earliest_date: dateMinus(2200), latest_date: BASE_DATE },
    // 與後端 dashboard_queries.get_meta() 的 wellness_metric_defs 一致，
    // 由 METRIC_DEFS 與 WELLNESS_METRIC_LABELS 動態組出，避免第三份重複清單。
    wellness_metric_defs: METRIC_DEFS.map(function (def) {
      var meta = WELLNESS_METRIC_LABELS[def.key];
      return {
        key: def.key,
        label: meta.label,
        unit: meta.unit,
        decimals: def.decimals,
        format: meta.format,
        default_hidden: WELLNESS_METRICS_DEFAULT_HIDDEN.indexOf(def.key) !== -1
      };
    }),
    notice: '此服務僅供區網存取且無身分驗證，切勿對外網開放。（目前顯示的是開發用虛構假資料）'
  };

  /**
   * 模擬 fetch：依 path 回傳對應假資料。
   * app.js 只在 window.MOCK_API 存在且未被 ?mock=0 停用時才會使用。
   */
  window.MOCK_API = {
    isMock: true,
    /**
     * @param {string} path 例如 '/api/sessions?range=30d'
     * @returns {Promise<Object>}
     */
    request: function (path, body) {
      // body 只有 POST 端點（/api/sync）會帶；假資料一律回固定結果，
      // 故不需要依 body 分歧，收下只是為了與 app.js 的 apiPost 簽章一致。
      return new Promise(function (resolve, reject) {
        // 模擬網路延遲，讓載入中狀態看得見
        setTimeout(function () {
          try {
            resolve(route(path));
          } catch (err) {
            reject(err);
          }
        }, 120);
      });
    },

    /**
     * 聊天的非串流端點（coach.js 用）。
     * @param {string} path
     * @param {Object} options {method}
     * @returns {Promise<Object|null>}
     */
    chatRequest: function (path, options) {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          try {
            resolve(mockChatRoute(path, options));
          } catch (err) {
            reject(err);
          }
        }, 120);
      });
    },

    /**
     * 模擬 SSE 串流：逐字吐出教練回覆，中間插一次工具呼叫，
     * 讓面板的 text／tool_use／tool_result／done 四種事件都被走到。
     * @param {string} text 使用者送出的內容（含〔檢視脈絡〕行）
     * @param {function(string, Object)} onEvent
     * @returns {Promise<void>}
     */
    chatStream: function (text, onEvent) {
      var reply = [
        '這是**假資料**的教練回覆，用來驗證面板版面。',
        '',
        '- 逐字輸出正常',
        '- 工具 chip 可以展開',
        '- Markdown 清單有排版',
        '',
        '真實回覆需要設定 `ANTHROPIC_API_KEY`。'
      ].join(String.fromCharCode(10));
      var toolId = 'tu_mock_' + Date.now();

      return new Promise(function (resolve) {
        var steps = [];
        steps.push([200, function () {
          onEvent('tool_use', { id: toolId, name: 'get_today', input: {} });
        }]);
        steps.push([400, function () {
          onEvent('tool_result', {
            tool_use_id: toolId, name: 'get_today', is_error: false,
            content: JSON.stringify({ date: BASE_DATE, note: '假資料' }, null, 2)
          });
        }]);
        // 逐字（其實是逐小段，避免上千個 timer）
        var chunkSize = 3;
        for (var i = 0; i < reply.length; i += chunkSize) {
          (function (slice) {
            steps.push([18, function () { onEvent('text', { delta: slice }); }]);
          })(reply.slice(i, i + chunkSize));
        }
        steps.push([80, function () {
          onEvent('done', { message_id: null, stop_reason: 'end_turn' });
          resolve();
        }]);

        var idx = 0;
        function next() {
          if (idx >= steps.length) return;
          var step = steps[idx++];
          setTimeout(function () { step[1](); next(); }, step[0]);
        }
        next();
      });
    }
  };


  // ---------------------------------------------------------------- AI 教練（假資料）
  //
  // 讓聊天面板（coach.js）可以在沒有 ANTHROPIC_API_KEY、甚至沒有後端的情況下
  // 離線開發與驗證版面。串流用本地假產生器逐字吐字，並含一次假的工具呼叫，
  // 好讓「工具 chip 可展開」這條 UI 行為也能被實際看到。

  var MOCK_CONVERSATIONS = [
    { id: 901, title: '今天該練嗎', created_at: BASE_DATE + 'T08:00:00+00:00',
      updated_at: BASE_DATE + 'T08:03:00+00:00' }
  ];
  var mockNextConvId = 902;

  // 對話 id → 已存的訊息（Anthropic content blocks 格式，與後端一致）
  var MOCK_MESSAGES = {
    901: [
      { id: 1, seq: 1, role: 'user',
        content: [{ type: 'text', text: '今天該練嗎？' }],
        created_at: BASE_DATE + 'T08:00:00+00:00' },
      { id: 2, seq: 2, role: 'assistant',
        content: [
          { type: 'tool_use', id: 'tu_mock_1', name: 'get_today', input: {} },
          { type: 'text', text: '' }
        ],
        created_at: BASE_DATE + 'T08:01:00+00:00' },
      { id: 3, seq: 3, role: 'user',
        content: [{
          type: 'tool_result', tool_use_id: 'tu_mock_1', is_error: false,
          content: '{"date":"' + BASE_DATE + '","planned":{"workout_type":"tempo"}}'
        }],
        created_at: BASE_DATE + 'T08:01:30+00:00' },
      { id: 4, seq: 4, role: 'assistant',
        content: [{ type: 'text', text:
          '我查到的事實：\n\n- 今天課表是**節奏跑** 5.4 km\n- 準備度：此日期無資料\n\n'
          + '（這是開發用假資料，不是真實建議。）' }],
        created_at: BASE_DATE + 'T08:03:00+00:00' }
    ]
  };

  function mockChatRoute(path, options) {
    var qIndex = path.indexOf('?');
    var pathname = qIndex === -1 ? path : path.slice(0, qIndex);
    var method = (options && options.method) || 'GET';

    if (pathname === '/api/chat/status') {
      return {
        enabled: true, reason: null,
        model: 'claude-sonnet-5（假資料）', client_allowed: true
      };
    }
    if (pathname === '/api/chat/conversations') {
      if (method === 'POST') {
        var conv = {
          id: mockNextConvId++, title: null,
          created_at: BASE_DATE + 'T09:00:00+00:00',
          updated_at: BASE_DATE + 'T09:00:00+00:00'
        };
        MOCK_CONVERSATIONS.unshift(conv);
        MOCK_MESSAGES[conv.id] = [];
        return conv;
      }
      return { conversations: MOCK_CONVERSATIONS };
    }

    var m = pathname.match(/^\/api\/chat\/conversations\/(\d+)$/);
    if (m) {
      var id = parseInt(m[1], 10);
      if (method === 'DELETE') {
        MOCK_CONVERSATIONS = MOCK_CONVERSATIONS.filter(function (c) { return c.id !== id; });
        delete MOCK_MESSAGES[id];
        return null;
      }
      var found = MOCK_CONVERSATIONS.filter(function (c) { return c.id === id; })[0];
      if (!found) throw new Error('找不到此對話（假資料）');
      var copy = {};
      Object.keys(found).forEach(function (k) { copy[k] = found[k]; });
      copy.messages = MOCK_MESSAGES[id] || [];
      return copy;
    }

    throw new Error('假資料未實作此聊天路徑：' + pathname);
  }

  function route(path) {
    var qIndex = path.indexOf('?');
    var pathname = qIndex === -1 ? path : path.slice(0, qIndex);
    var params = new URLSearchParams(qIndex === -1 ? '' : path.slice(qIndex + 1));
    var range = params.get('range') || '30d';

    if (pathname === '/api/meta') return META;
    if (pathname === '/api/sessions') return filterSessions(range);
    if (pathname === '/api/wellness-trend') return buildWellness(range);
    if (pathname === '/api/training-days') return buildTrainingDays(range);
    if (pathname === '/api/recovery-impact') return buildRecoveryImpact(range);
    if (pathname === '/api/today') return buildToday();
    if (pathname === '/api/calendar') {
      var start = params.get('start') || dateMinus(6);
      var end = params.get('end') || BASE_DATE;
      return buildCalendar(start, end);
    }
    if (pathname === '/api/training-load') return buildTrainingLoad(range);
    // 假資料模式沒有 Garmin token，同步永遠不可用——回 client_allowed:false
    // 讓按鈕維持停用，而不是讓人在 demo 模式按下去打真的 API。
    if (pathname === '/api/sync/status') {
      return { client_allowed: false, reason: '假資料模式（?mock=1）不提供資料同步' };
    }
    if (pathname === '/api/sync') {
      return { available: false, reason: '假資料模式（?mock=1）不提供資料同步' };
    }

    var m = pathname.match(/^\/api\/sessions\/(\d+)$/);
    if (m) {
      var detail = buildSessionDetail(parseInt(m[1], 10));
      if (!detail) throw new Error('找不到此場次（假資料）');
      return detail;
    }
    throw new Error('假資料未實作此路徑：' + pathname);
  }
})();
