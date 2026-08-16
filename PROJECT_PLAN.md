# AI 跑步教練專案計畫

## 0. 決策記錄（Decisions Log）

> 2026-08-16 討論後拍板，之後若要推翻請在此更新並說明原因。

| 決策項目 | 結論 | 原因 |
|---|---|---|
| Garmin 帳號風險 | **不先做即時 MCP 串接**，Phase 1 MVP 只用手動歷史資料匯入（Garmin 官方匯出 + 手動 FIT），不涉及帳密登入 | 主目標是先把教練（Phase 2/3）做出來；MCP/Playwright 繞過 Cloudflare 的帳號風險留到真的要做即時同步時再測試 |
| MVP 範圍 | 縮小：Phase 1 只做 1A（歷史資料回補）+ 1C（統一 schema），1B（三層即時同步備援）與去重邏輯延後 | 避免資料工程份量壓過主目標，讓 Phase 2/3 儘快有真實資料可驗證 |
| 對話介面（Claude Desktop / Claude Code / 自建） | 暫不決定，先開發 MCP server 本體，等做到 Phase 3 再選介接方式 | 介面選擇不影響 server/tools 的設計，可以晚點決定 |
| 憑證管理（1D） | 先用 `.env`（不進 git），且因 MVP 手動匯入不需要帳密，**實際上可以再延後**，等進到 Phase 1B（即時同步）才需要真的實作 | 求簡單、避免過早設計用不到的東西 |

## 1. 專案願景與核心目標

**主目標**：打造一套 AI coach，能評估「訓練品質」與「身體數據」，並據此給出跑步訓練建議與課表調整。

**次要支撐項目**：Garmin 手錶資料串接——這是為了讓 AI coach 有真實數據可分析的**工作項目之一**，不是專案本身的目的。規劃時的資源分配應以 Phase 3（AI 分析層）為核心，資料工程只是把地基打好。

**使用情境**：目前為個人使用，但憑證管理等設計需預留未來可能公開／多使用者的彈性。

---

## 2. 整體架構

```
[歷史資料]                          [持續資料]
Garmin 官方個人資料匯出              garmin-connect-mcp（主）
  │                                    │ session過期/結構性失效
  │ GarminDB 或客製 parser              ▼
  │                                  Strava API（備援，帳號已開自動同步）
  │                                    │ 仍有缺口
  │                                    ▼
  │                                  手動 FIT 匯入（最終備援）
  │                                    │
  └──────────────┬─────────────────────┘
                  ▼
         統一本地 SQLite（含 source / 完整度標記）
                  │
                  ▼
     訓練科學規則引擎（VDOT、週期化課表、訓練負荷、純程式邏輯）
                  │
                  ▼
     MCP Server（把資料查詢 + 規則引擎包成 tools）
                  │
                  ▼
     Claude 對話：訓練品質評估、身體數據分析、課表微調建議 ★主目標
```

**設計原則**：核心邏輯（資料、規則引擎）與 AI 介面層（MCP/Skill）脫鉤，資料擷取來源本身也做成可替換 adapter——因為 Garmin 的反爬蟲手段會持續變動（garth 於 2026年3月因 Garmin 加入 Cloudflare TLS 指紋辨識而停止維護即為一例）。

---

## 3. 專案 TODO / Milestones

### Phase 0 — 需求確認（blocking）

**身體狀況**
- [ ] 基本身體數據（年齡、身高、體重）
- [ ] 跑步年資與目前訓練量（跑步多久了、目前每週次數/公里數）
- [ ] 傷病史（膝蓋、腳踝、髂脛束、足底筋膜炎等跑者常見傷害，舊傷或現況）
- [ ] 心率數據（靜止心率 RHR、最大心率是實測還是年齡公式推算）
- [x] 每週訓練頻率：3-5 次，有一定基礎
- [ ] 既有數據完整度（Garmin/Strava 歷史資料大約涵蓋多久、睡眠時是否配戴手錶）

**比賽計畫**
- [ ] 目標賽事名稱與日期（全馬）
- [ ] 目標完賽時間（單純完賽／破 4／破 3.5／BQ 等具體目標）
- [ ] 近期比賽成績（半馬/全馬/10K 等，用於換算 VDOT 起始配速區間）
- [ ] 每週可訓練時段與偏好（平日/假日、晨跑/晚跑）
- [ ] 特殊限制（工作出差週期、季節氣候如夏訓、場地限制如跑步機、是否已有教練/跑團在指導）

### Phase 1 — 資料基礎建設（Garmin 資料導入 subtask）

> **MVP 範圍**：本階段先只做 1A + 1C，讓 Phase 2/3 儘快有真實資料可用。1B（即時同步三層備援）與 1D（憑證管理）延後到之後真的要做即時同步時再啟動，詳見「0. 決策記錄」。

**1A. 歷史資料回補（MVP，先做）**
- [ ] 向 Garmin Connect 申請官方個人資料匯出（帳號設定 → Export Your Data，GDPR/CCPA 機制，零風險但為一次性快照）
- [ ] 評估 [GarminDB](https://github.com/tcgoetz/GarminDB)（持續維護中，v3.6.x）能否直接解析匯出包＋FIT 檔到 SQLite，優先重用而非重寫
- [ ] 若 GarminDB 涵蓋不到的欄位（如特定 DI-Connect-Wellness JSON 內容），評估是否需要客製 parser 補強
- [ ] 統一寫入本地標準化 schema（見 1C）

**1C. 統一資料模型與品質標記（MVP，先做）**
- [ ] 設計 SQLite schema：`activities`、`daily_wellness`、`plan`、`source_metadata`
- [ ] 加入 `source`（fit_manual / garmin_export，之後才加 garmin_mcp / strava）與 `has_wellness_data` 等完整度欄位
- [ ] 記錄每筆資料擷取時間與來源版本，方便除錯與追蹤資料品質

---

**1B. 持續同步（三層備援）——延後**
- [ ] 主力：評估/整合 [etweisberg/garmin-connect-mcp](https://github.com/etweisberg/garmin-connect-mcp)（現成 27+ 工具，Playwright 真瀏覽器繞過 Cloudflare）或參考其設計自建
- [ ] 釐清並實作「session 過期（例行重新登入）」vs「結構性失效（需切換 Strava）」的判斷邏輯，避免小問題觸發整條備援鏈
- [ ] 備援：Strava API（帳號已開自動同步）——註冊 Strava API App、取得 client credentials、實作 OAuth token 管理與 refresh
- [ ] 設計來源切換後的資料去重邏輯（時間+距離+時長指紋比對，避免 Strava 補的資料跟 MCP 回補的歷史重複）
- [ ] 單位／時區正規化層（三種來源格式不一致）

**1D. 憑證與機密管理——延後（進 1B 時才需要）**
- [ ] 本地機密儲存策略（`.env` 不進 git，或用 Windows Credential Manager）
- [ ] 為未來可能公開專案預留「每位使用者自帶自己憑證」的設計——不可硬編碼任何 token，需有清楚的 setup 說明
- [ ] `.gitignore` 與 `.env.example` 範本

### Phase 2 — 訓練科學規則引擎（純程式邏輯，不依賴 AI）
- [ ] VDOT/Daniels 配速區間計算模組
- [ ] 週期化課表產生器（Base/Build/Peak/Taper，全馬 16–20 週框架）
- [ ] 訓練負荷計算（ATL/CTL/TSB 概念，或對接 Garmin 既有 Training Load）
- [ ] 恢復狀態判斷邏輯（HRV/RHR/睡眠趨勢），需能處理欄位缺失時的降級判斷（呼應 1C 完整度標記）

### Phase 3 — AI Coach 分析層（★ 專案主目標所在，資源應優先集中於此）
- [ ] 設計 MCP server，把 Phase 1 資料查詢 + Phase 2 規則引擎包成 tools
- [ ] 定義「訓練品質評估」的分析維度：配速執行度、心率漂移、恢復是否充足、課表遵從度等
- [ ] 對話式教練互動：查詢近況、解釋建議原因、動態微調課表
- [ ] 建立評估回饋迴圈：AI 建議 → 實際執行 → 下次分析納入偏差比對

### Phase 4 — 之後可做（非 MVP）
- [ ] 視覺化 dashboard
- [ ] 多使用者／公開發佈準備（若決定開源）
- [ ] 訓練提醒／通知機制

---

## 4. 風險與待觀察

- Garmin 反爬蟲手段持續在變（garth 停維護即為前例），MCP/API 方案可能再度失效，架構需保留備援彈性。
- GarminDB、garmin-connect-mcp 皆為第三方維護專案，非官方保證長期可用。
- Strava 缺乏 Garmin 專屬生理指標（HRV、Body Battery、Training Readiness），fallback 期間分析品質會降級，需在 UI/對話中誠實反映。

## 5. 待確認事項（Open Items）

完整清單見「Phase 0 — 需求確認」（身體狀況 5 項＋比賽計畫 5 項），避免重複維護。
