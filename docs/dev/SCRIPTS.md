# 常用腳本指令

> 啟動 Dashboard 與匯出離線 HTML 的指令整理。以下都在**專案根目錄**執行。
> 離線檔的設計、iPhone 捷徑設定見 [OFFLINE_EXPORT.md](OFFLINE_EXPORT.md)。

---

## 1. 啟動 Dashboard

腳本：`scripts\dashboard.cmd`

| 指令 | 說明 |
|---|---|
| `scripts\dashboard` | 啟動 API，區網可連（手機可開），port 8000，就緒後自動開瀏覽器 |
| `scripts\dashboard --host 127.0.0.1 --no-browser` | 只限本機、不開瀏覽器 |
| `scripts\dashboard --port 8790` | 換 port |

- 額外參數會原樣轉給 `src.main.python.api.app`，後面的 `--host`／`--port` 會蓋掉前面的。
- ⚠️ 預設綁 `0.0.0.0` 且**沒有身分驗證**，只能放在可信任的家用區網，不要對外網開放。

---

## 2. 匯出離線 HTML

腳本：`scripts\export_offline.cmd`（內部呼叫 `scripts/build_dashboard_offline.py`）

| 指令 | 說明 |
|---|---|
| `scripts\export_offline` | 匯出到預設資料夾，完整歷史 |
| `scripts\export_offline --detail-months 12` | 單場明細只留近 12 個月（舊的長跑仍保留，見下） |
| `scripts\export_offline D:\某資料夾` | 匯出到指定資料夾 |
| `scripts\export_offline D:\某資料夾 --detail-months 12` | 指定資料夾＋12 個月 |
| `scripts\export_offline --detail-months 12 --keep-long-km 30` | 改長跑門檻 |

**參數規則**：第一個參數不是以 `-` 開頭，就當成目的資料夾；其餘參數原樣轉給匯出腳本。

**預設資料夾**：`%USERPROFILE%\iCloudDrive\HTML-decode`，不存在會自動建立。

**輸出**：
- 檔名帶匯出日期，例如 `dashboard_offline_2026-10-24.html`。
- 目的資料夾與專案的 `output\` 各有一份，只有 html，沒有 txt。
- 同一天重跑會覆蓋；不同天的舊檔不會自動清掉。
- iPhone 捷徑「取得檔案」的路徑是寫死檔名，日期每天不同，所以需要改路徑，或改成選資料夾後取最新一個。

### 單場明細的保留規則（`--detail-months`）

- 近 N 個月的場次：保留單場明細（逐秒圖）。
- 更早的場次：預設只有距離 **≥21 km**（半馬以上）的長跑保留明細，不受月數限制，其餘只剩清單摘要。
- 門檻用 `--keep-long-km` 調整，`0` 表示不保留。
- 不帶 `--detail-months` 時，全部場次都有明細，此規則不影響。
- 舊場次的明細只有從「活動紀錄」清單點進去看得到；行事曆只涵蓋近 N 個月。

### 環境變數

| 變數 | 預設 | 用途 |
|---|---|---|
| `RC_PYTHON` | `%USERPROFILE%\anaconda3\envs\rc\python.exe` | conda 環境 `rc` 的直譯器 |
| `RC_DB_PATH` | `output\running_coach.db` | 來源資料庫（只讀） |
| `RC_OFFLINE_EXPORT_DIR` | `%USERPROFILE%\iCloudDrive\HTML-decode` | 預設目的資料夾 |

### 直接呼叫 Python

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" scripts/build_dashboard_offline.py --db-path output/running_coach.db --copy-to "<資料夾>" --dated
```

| 參數 | 說明 |
|---|---|
| `--db-path` | 資料庫路徑，預設 `output/running_coach.db` |
| `--out` | 輸出 HTML 路徑，預設 `output/dashboard_offline.html` |
| `--copy-to` | 另外複製一份到這個資料夾；預設讀 `RC_OFFLINE_EXPORT_DIR`，沒設就不複製 |
| `--dated` | 檔名加上匯出日期（`export_offline` 預設帶） |
| `--detail-months N` | 只保留近 N 個月的單場明細，`0` 為全部 |
| `--keep-long-km KM` | 搭配 `--detail-months`：更早的場次距離達 KM 以上仍保留明細，預設 21 |
| `--txt` | 同時輸出內容相同的 `.txt`（`export_offline` 不帶） |
| `--no-gzip` | 快照不壓縮，除錯用 |
| `--athlete-id` | 學員 ID，預設第一位 |
| `--profile-path` | 學員檔（賽事名稱來源） |

---

## 3. 跑測試

```bash
RC="C:/Users/cFu/anaconda3/envs/rc/python.exe"
"$RC" -m unittest discover -s src/test/unit -p "test_*.py" -v
"$RC" -m unittest src.test.unit.test_build_dashboard_offline   # 只跑離線匯出
```
