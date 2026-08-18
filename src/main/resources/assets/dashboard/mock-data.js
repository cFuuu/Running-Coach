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

  function buildHrZones(session, seed) {
    var rnd = makeRandom(seed);
    var zones = [];
    var remain = session.duration_sec;
    for (var z = 0; z <= 5; z += 1) {
      var share = z === 0 ? 0.04 : (z === 5 ? 0.03 : 0.12 + rnd() * 0.2);
      var sec = Math.round(session.duration_sec * share);
      if (sec > remain) sec = Math.max(remain, 0);
      remain -= sec;
      zones.push({ zone: z, seconds: sec });
    }
    return { available: true, zones: zones };
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

  // ---- 每日身體狀況（八指標；契約排除的電池類指標一律不產生）----
  // coverage 刻意設定成不同起日，讓 range=1y/all 時部分指標 clipped:true
  var METRIC_DEFS = [
    { key: 'hrv_ms', base: 46, spread: 9, decimals: 1, coverageDaysAgo: 300, gapMod: 9 },
    { key: 'resting_hr_bpm', base: 49, spread: 5, decimals: 0, coverageDaysAgo: 900, gapMod: 13 },
    { key: 'spo2_pct', base: 96, spread: 2, decimals: 0, coverageDaysAgo: 420, gapMod: 7 },
    { key: 'sleep_score', base: 76, spread: 14, decimals: 0, coverageDaysAgo: 900, gapMod: 11 },
    { key: 'training_readiness_score', base: 64, spread: 18, decimals: 0, coverageDaysAgo: 260, gapMod: 8 },
    { key: 'stress_avg', base: 28, spread: 10, decimals: 0, coverageDaysAgo: 900, gapMod: 12 },
    { key: 'all_day_stress_avg', base: 38, spread: 12, decimals: 0, coverageDaysAgo: 180, gapMod: 10 },
    { key: 'steps', base: 9200, spread: 4200, decimals: 0, coverageDaysAgo: 2100, gapMod: 17 }
  ];

  function buildWellness(range) {
    var days = RANGE_DAYS[range] || 30;
    var metrics = {};
    METRIC_DEFS.forEach(function (def, idx) {
      var rnd = makeRandom(1000 + idx * 37);
      var earliest = dateMinus(def.coverageDaysAgo);
      var points = [];
      for (var d = days - 1; d >= 0; d -= 1) {
        var date = dateMinus(d);
        if (date < earliest) continue;              // 早於 coverage 的日期完全沒有點
        if (d % def.gapMod === 3) continue;         // 刻意缺值 → 前端應斷線，不補 0
        var v = def.base + (rnd() - 0.5) * 2 * def.spread + Math.sin(d / 11) * def.spread * 0.35;
        var value = def.decimals === 1 ? Math.round(v * 10) / 10 : Math.round(v);
        points.push({ date: date, value: value });
      }
      points.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
      metrics[def.key] = {
        available: points.length > 0,
        clipped: days > def.coverageDaysAgo,
        coverage: { earliest_date: earliest, latest_date: BASE_DATE },
        points: points
      };
      if (points.length === 0) {
        metrics[def.key].reason = '此區間無資料';
      }
    });
    return {
      range: range,
      start_date: dateMinus(days - 1),
      end_date: BASE_DATE,
      metrics: metrics
    };
  }

  // ---- 訓練日 ----
  function buildTrainingDays(range) {
    var days = RANGE_DAYS[range] || 30;
    var start = dateMinus(days - 1);
    var out = [];
    ALL_SESSIONS.forEach(function (s) {
      if (s.date >= start && s.date <= BASE_DATE && out.indexOf(s.date) === -1) out.push(s.date);
    });
    out.sort();
    return { range: range, training_days: out };
  }

  // ---- 恢復影響 ----
  function buildRecoveryImpact(range) {
    var days = RANGE_DAYS[range] || 30;
    var start = dateMinus(days - 1);
    var impacts = [];
    ALL_SESSIONS.forEach(function (s) {
      if (s.date < start || s.date > BASE_DATE) return;
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

  function filterSessions(range) {
    var days = RANGE_DAYS[range] || 30;
    var start = dateMinus(days - 1);
    var list = ALL_SESSIONS.filter(function (s) { return s.date >= start && s.date <= BASE_DATE; });
    return {
      range: range,
      start_date: start,
      end_date: BASE_DATE,
      sessions: list
    };
  }

  var META = {
    athlete: { id: 1, name: '示範跑者' }, // 虛構名稱，非真實使用者
    metric_coverage: {
      hrv_ms: { earliest_date: dateMinus(300), latest_date: BASE_DATE },
      activities: { earliest_date: dateMinus(2200), latest_date: BASE_DATE }
    },
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
    request: function (path) {
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
    }
  };

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

    var m = pathname.match(/^\/api\/sessions\/(\d+)$/);
    if (m) {
      var detail = buildSessionDetail(parseInt(m[1], 10));
      if (!detail) throw new Error('找不到此場次（假資料）');
      return detail;
    }
    throw new Error('假資料未實作此路徑：' + pathname);
  }
})();
