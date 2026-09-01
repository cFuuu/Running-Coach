# 生理閾值研究（N-3 batch 1）

> **建立日期**：2026-08-29
> **範圍**：與訓練方法論（Daniels／Hansons／Higdon 等）**無關**的生理閾值。訓練計畫結構已在 N-6 另案定案，本文不涉及。
> **定位**：專案決策紀錄。凡是「教練經驗法則、無同儕審查依據」的數字，本文一律直說，不包裝成科學。查不到的一律寫「未能查證」。

---

## 結論摘要

### 1. Cardiac drift / 心率漂移閾值

**5% 這個數字沒有同儕審查依據，是 Joe Friel 經由 TrainingPeaks 推廣的教練經驗法則。** 更關鍵的是：Friel 的 5% 定義在 **Pa:HR（配速÷心率的效率比值）** 之上，而本專案 `compute_hr_drift()` 算的是**單純前後半平均心率差**——後者完全沒有把配速納入分母，兩者不是同一個量，5% 不能直接套用。同儕審查文獻（Coyle & González-Alonso 2001）反而指出：在**正常**的長時間次最大強度運動中，心率本來就會漂移，thermoneutral 條件下 60–75% VO₂max 跑 1–2 小時漂個位數百分比是**生理常態而非異常**。建議：**不設「異常」閾值**，只呈現數值與趨勢；若一定要設觸發線，須先改算 Pa:HR，且僅限「時長 ≥ 45–60 分鐘、穩定配速、easy/aerobic 強度」的活動。

### 2. 配速執行容差

**沒有任何同儕審查文獻定義「配速偏差多少算沒照課表執行」。** Daniels 有給的是**課表處方的區間**，不是事後判讀的容差——E pace 本身就是一個範圍（約 ±20 sec/mile ≒ ±12 sec/km），而 I/R pace 因為超過 VO₂max 配速無額外效益，容差窄得多。環境修正方面，**有**可用的公開模型：Daniels/VDOT 的「氣溫＋露點」規則（超過 130°F 合計值後，每多 10 點慢約 1%）屬 practitioner-authority；同儕審查層級則有 Ely et al. 2007（WBGT 每升高一個區間馬拉松成績漸進變慢，且**慢的跑者受罰更重**）。建議：easy 用區間判定不設點目標；quality 課表用 ±3% 作為工程決策（**無文獻背書**），並在氣溫＋露點超標時先做環境修正再判定。

### 3. TSB 閾值 —— **尺度依賴假設「成立」**

**確認：TrainingPeaks 的 −30／−10／+25 是定義在 TSS 尺度上的數字，不是無因次的。** TSS 的定義是「1 小時 functional threshold 強度 = 100」，−30 代表「約 0.3 個 threshold 小時的負荷落差」，這個物理意義完全綁在 TSS 的單位上。本專案的負荷單位是 `duration_seconds × %HRR`——1 小時 threshold 強度約為 `3600 × 0.85 ≈ 3060`，比 TSS 尺度大約 **30 倍**。所以 −30 在本專案尺度上約等於 TrainingPeaks 的 **−1**，也就是幾乎等於 0，這完全解釋了為何它會在 55% 的日子觸發。**必須重新推導，不能沿用。** 至於「改用相對量」：ACWR 這條路**不建議**，因為 Gabbett 的原始主張已遭 Impellizzeri et al. 系統性駁斥（數學耦合、sweet spot 圖表被要求撤稿）。另外，7/42 天時間常數本身也**只是慣例**——Hellard et al. 實測個體擬合的時間常數 CV 超過 30%，明言「提出單一組參數可能是可疑的」。

### 4. HRV 下降閾值

**文獻不支持「相對 7 日均值下降 >15%」這種形式。** HRV 監控文獻（Plews、Buchheit、Kiviniemi 一系）的標準做法是：先對 rMSSD 取自然對數（**LnrMSSD**），再用 **7 日滾動平均**，並以個人化的 **SWC（smallest worthwhile change）= 0.5 × 個人基線 SD 或 CV** 作為正常範圍界線。取對數這件事直接推翻了百分比形式的正當性：對數尺度上的「固定差值」對應原始尺度上的「固定比值」，兩者不能混用。基線需求：實務協定用 **4 週**標準化期建立基線並**每週更新**，且需**每週至少 3 個有效測量點**。本專案 292 天資料量足夠，但**測量頻率**才是真正的關卡。建議改為 LnrMSSD 的 SWC 判定。

---

## 主題一：Cardiac drift / Pw:HR decoupling

### 5% 從哪來？

5% 這個數字的源頭是 **Joe Friel**，經由 TrainingPeaks codify 成 Pw:HR（功率÷心率，自行車）與 Pa:HR（配速÷心率，跑步）的判讀基準。Friel 在 TrainingPeaks 教練部落格給的分級是：

- **< 5%**：該強度下有紮實的有氧耐力
- **5–10%**：中度耐力限制或疲勞
- **> 10%**：該次強度很可能已超過有氧閾值

（來源：[TrainingPeaks / Joe Friel — Aerobic Endurance and Decoupling](https://www.trainingpeaks.com/coach-blog/aerobic-endurance-and-decoupling/)）

**這是教練經驗法則，不是從研究推導出來的閾值。** 該文確實列了數篇心血管漂移的同儕審查引文，但那些文獻支持的是「心率會漂移」這個生理現象，**不是** 5% 這條特定的分界線。我未能找到任何同儕審查研究把 5% 驗證為診斷性 cut-off（判別效力、敏感度／特異度都沒有）。查證上有個限制：TrainingPeaks 官方 Help Center 的 Pa:HR 頁面回 HTTP 403，無法直接取得其原始定義文字，上述分級來自其教練部落格。

### Pw:HR vs Pa:HR vs 本專案算的東西

這是本主題最重要的一點。三者不是同一個量：

| 指標 | 分子/分母 | 意義 |
|---|---|---|
| Pw:HR | 功率 ÷ 心率，前後半比值變化 | 輸出功穩定下，心率成本上升多少 |
| Pa:HR | 配速 ÷ 心率，前後半比值變化 | 同上，跑步版 |
| **本專案 `compute_hr_drift()`** | **(後半平均HR − 前半平均HR) / 前半平均HR** | **只有心率，完全沒有配速** |

`src/main/python/services/fit_parser.py:136` 的實作沒有把配速納入計算。這代表：**如果跑者後半段自己跑快了，心率上升會被記成「漂移」**，但 Pa:HR 會正確地把它視為「配速也上升了，效率其實沒掉」。兩者在變速跑、漸速跑（progression run）、有坡度的路線上會給出方向相反的結論。

因此 **5% 不能轉移到本專案目前算的指標上**。若要用 Friel 的閾值，前提是先把指標改成 Pa:HR，而且跑步還需要 **grade-adjusted pace（GAP）**——上坡每公尺耗費的心跳數本來就高於下坡，未做坡度校正的 raw pace 會污染比值。

### 干擾因子（會讓讀數失效的條件）

同儕審查層級的證據：

- **Coyle & González-Alonso (2001)**，[PubMed 11337829](https://pubmed.ncbi.nlm.nih.gov/11337829/)：長時間運動中的 cardiovascular drift 是**正常生理現象**——每跳輸出量（stroke volume）下降、心率代償上升。他們主張主要機制是心率上升導致舒張期充填時間縮短，而非傳統認為的皮膚與肌肉爭奪血流。
  （查證限制：PubMed 頁面因 cookie 牆無法直接抓取全文摘要，此處內容來自搜尋結果彙整與該文廣為引用的標準結論，未能逐字核對原文。）
- **Montain & Coyle (1992)**：漂移幅度與脫水程度（體重流失百分比）**成正比**。體重流失超過約 **2%** 時漂移明顯加速。實驗中完全不補水者心率上升約 **10%**，補水量與流汗速率相當者僅上升約 **5%**。
  （查證限制：此為二手來源轉述的數字，未能取得原文逐字核對。）
- **正常長跑的基線漂移**：thermoneutral 條件、訓練有素者、60–75% VO₂max、120 分鐘，漂移幅度**平均 8–12%**。
  （查證限制：此數字來自二手整理來源，未能追溯到原始論文，**引用時應保留懷疑**。但方向性結論——正常長跑就會漂移到遠超過 5%——與 Coyle 的機制論述一致。）

**這一點對本專案的判讀影響極大**：如果正常長跑的心率漂移本來就有 8–12%，那麼用 5%（甚至任何個位數）當「異常」門檻，會把絕大多數正常長跑標成異常。這與 TSB −30 觸發 55% 天數是同一類錯誤。

其餘干擾因子（practitioner 層級，但機制上無爭議）：熱／濕度、脫水、燃料不足（醣類耗竭）、睡眠不足、咖啡因、心率帶接觸不良造成的前段訊號漂移。

### 時長／強度下限

Friel 的建議適用範圍是「長時間有氧課表」：自行車 2–4 小時、**跑步 1–2 小時，於有氧閾值強度**（來源同上，practitioner 層級）。

**我未能找到任何同儕審查來源明定「低於幾分鐘就不該計算」的下限。** 但從指標本身的建構邏輯可以推出無爭議的必要條件：

- 該指標假設**前後半的外部負荷相同**。間歇課表、法特雷克、漸速跑根本不滿足這個前提，算出來的數字沒有意義。
- 短時間活動中，暖身期的心率上升會主導前半段平均，使漂移被系統性高估。

工程上合理的下限（**這是工程決策，非文獻推導**）：時長 ≥ 45–60 分鐘、配速變異係數低（穩定跑）、強度落在 easy/aerobic 區間。

---

## 主題二：配速執行容差

### Daniels 有沒有給容差？

**沒有「執行判定容差」。** Daniels 給的是**處方區間**，這在概念上是不同的東西——區間是「你該跑在這裡面」，容差是「你偏離多少才算沒做到」。可查到的相關數字：

- **E（easy）pace**：本來就是範圍，不是點目標。約 65–78% VO₂max。實務轉述指出某天快慢差到 **±20 sec/mile（≒ ±12 sec/km）** 仍在可接受範圍內。
- **I（interval）pace**：95–100% VO₂max / 98–100% HRmax。Daniels 明確主張**跑超過 VO₂max 配速沒有額外效益**，只會累積無用壓力並拖慢後續組數——所以 I 的容差是**不對稱的**：偏慢比偏快更可容忍。
- **訓練期程進程**：前 6 週用比目標配速慢 10 sec/mile（6 sec/km）的配速，中間 6 週收斂到目標的 4 sec/mile（2.5 sec/km）以內。

（來源：[Fellrnr — Jack Daniels](https://fellrnr.com/wiki/Jack_Daniels)、[Coach Ray — Jack Daniels' Running Intensity](https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/)，皆為 secondary，非原書逐字。**查證限制：我沒有《Daniels' Running Formula》原書可核對，上述數字全部來自二手轉述。**）

**沒有任何同儕審查文獻定義「配速偏差 X% 即判定未執行」。** 這個判定本質上是產品設計決策，不是生理學問題。

### 容差是否隨課表類型不同？

是，而且方向明確：

| 課表類型 | 處方形式 | 容差邏輯 |
|---|---|---|
| Easy / Long | **區間** | 落在區間內即達成；偏快才是問題（easy 跑太快是最常見的訓練錯誤），偏慢幾乎無害 |
| Threshold | 窄區間 | 雙向都要收斂——太快就不是 threshold 了，會變成 VO₂max 課 |
| Interval / Rep | 點目標 + 上限 | 不對稱：偏快**無益且有害**，偏慢則稀釋刺激 |

### 環境修正公式

**有兩個層級的來源。**

**(A) Practitioner-authority — Daniels / VDOT「氣溫 + 露點」規則**

規則形式：把**氣溫（°F）與露點（°F）相加**，合計值超過 **130** 之後，每再高 10 點，配速約慢 **1%**。

```
adjustment_pct ≈ max(0, (temp_F + dewpoint_F - 130) / 10) × 1%
```

用露點而非相對濕度是有物理理由的：露點才決定汗水能否蒸發散熱，相對濕度百分比在不同氣溫下意義不同。

（來源：[V.O2 / VDOT News — Adjust Your Training Paces For High Temperatures](https://news.vdoto2.com/2015/07/adjust-your-training-paces-for-high-temperatures/)、[RunnersConnect — Dew Point Effect](https://runnersconnect.net/dew-point-effect-running/)。**查證限制：這條規則的精確係數在不同二手來源間略有出入，且我未能取得 Daniels 原書或 VDOT 官方演算法規格逐字核對。台灣氣候長期落在此規則的觸發區間內，套用前建議先用本專案自己的歷史資料驗證係數。**）

**(B) Peer-reviewed — Ely et al. (2007)**

Ely ML, Cheuvront SN, Roberts WO, Montain SJ. *Impact of weather on marathon-running performance.* Med Sci Sports Exerc, 2007. [PubMed 17473775](https://pubmed.ncbi.nlm.nih.gov/17473775/)

- 資料規模：7 場馬拉松（Boston、New York、Twin Cities、Grandma's、Richmond、Hartford、Vancouver），合計 **140 race-years**。
- 依 **WBGT** 分四個區間：Q1 5.1–10°C、Q2 10.1–15°C、Q3 15.1–20°C、Q4 20.1–25°C。
- 結論：成績自 **WBGT 5–10°C 以上開始隨氣溫升高漸進變慢**；男女受影響程度相似；**較慢的跑者受到的成績懲罰大於菁英跑者**。
- 相關研究指出 WBGT 到達最高區間時，即使菁英男子選手也比賽道紀錄慢約 **4.5%**。

**「慢跑者受罰更重」這點對本專案直接相關**——任何環境修正若用單一係數套用到所有跑者，會系統性低估一般跑者受到的影響。

（**查證限制：我未能取得 Ely et al. 原文的迴歸方程式係數。** 該研究提供的是分區間的成績比較，不是一條可直接代入的連續修正公式。想要「代入溫濕度得到修正配速」的話，目前只有 (A) 的 practitioner 規則可用。）

---

## 主題三：TSB 閾值 —— 尺度依賴性

### 核心問題的答案：**是的，−30 是尺度依賴的，不能轉移**

TrainingPeaks 的 Performance Management Chart 定義：

- **CTL**（Fitness）：每日 TSS 的指數加權平均，時間常數 **42 天**
- **ATL**（Fatigue）：同上，時間常數 **7 天**
- **TSB**（Form）= 昨日 CTL − 昨日 ATL

Friel 在 TrainingPeaks 教練指南給的分區：

- **+15 ~ +25**：peak performance 區間
- **0**：訓練平衡點
- **−10 ~ −30**：理想訓練區
- **超過 −30**：極端負荷，需要連續休息日

（來源：[TrainingPeaks — A Coach's Guide to ATL, CTL & TSB](https://www.trainingpeaks.com/coach-blog/a-coachs-guide-to-atl-ctl-tsb/)，practitioner-authority）

**關鍵觀察**：該文把這些數字當成**普世指引**呈現，**完全沒有說明它們依賴於 TSS 尺度**。這正是本專案踩坑的原因——數字看起來像無因次常數，其實不是。

**為什麼它是尺度依賴的：** TSB = CTL − ATL 是一個**差值**，不是比值。差值必然帶有與被減數相同的單位。TSS 的定義是「1 小時 functional threshold 強度 = 100 TSS」，所以 −30 的物理意義是「急性負荷比慢性負荷高出約 0.3 個 threshold 小時份」。這個「0.3 小時」的意義只有在 TSS 的錨定下才成立。

**換算到本專案的尺度：** 本專案負荷 = `duration_sec × %HRR`。一小時 threshold 強度的活動 ≈ `3600 × 0.85 ≈ 3060` 單位，而同一次活動在 TSS 尺度上是 100。**尺度比約 30 倍。**

因此 TrainingPeaks 的 −30 在本專案尺度上，等價值應該是：

```
-30 × 30 ≈ -900
```

而本專案實測 TSB 範圍是 **−669 ~ +370**。也就是說，換算後的等價門檻 **−900 幾乎永遠不會觸發**，而目前用的 **−30 幾乎永遠觸發**（55% 的日子）。兩者都是同一個錯誤的兩端：**把有單位的數字當無因次常數搬運**。

（注意：30 倍只是數量級估算，實際比例會隨個人 HRmax/RHR 與訓練強度分布而變。重點是「不是 1」，而且差了一個數量級以上。）

### 那改用相對量（TSB/CTL 或 ACWR）好嗎？

**理論上方向正確，但 ACWR 這條特定路徑不建議採用，因為它已被同儕審查文獻嚴重駁斥。**

**原始主張 —— Gabbett (2016)**

Gabbett TJ. *The training-injury prevention paradox: should athletes be training smarter and harder?* Br J Sports Med. [PubMed 26758673](https://pubmed.ncbi.nlm.nih.gov/26758673/)｜[全文 PDF](https://efsma.org/images/pdf/publications/Br-J-Sports-Med-2016-Gabbett-273-80.pdf)

- ACWR = 前 1 週急性負荷 ÷ 前 4 週滾動平均慢性負荷
- **sweet spot 為 0.8–1.3**：受傷風險低
- **danger zone 為 > 1.5 或 < 0.8**：受傷風險升高

**方法學批判 —— Impellizzeri et al.**

- *Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls*（2020）：ACWR 存在**數學耦合（mathematical coupling）**——分子（急性負荷）本身是分母（慢性負荷）的組成部分，導致比值與結果之間出現虛假關聯；另有 confounding bias 問題。結論是「比值的統計性質使 ACWR 成為不準確的指標」，且**沒有證據支持把 ACWR 用於訓練負荷管理系統或減少傷害風險的訓練建議**。
- *The acute-chronic workload ratio-injury figure and its sweet spot are flawed*（2019）：直接要求**撤稿或更正** Blanch & Gabbett 的 ACWR–受傷機率圖表。指出**移除離群值並把資料當連續變項處理後，ACWR 與受傷之間的關聯就消失了**。
- *What Role Do Chronic Workloads Play in the Acute to Chronic Workload Ratio? Time to Dismiss ACWR and Its Underlying Theory*（2020）：標題已表明立場——建議放棄 ACWR 及其背後理論。

**（查證限制：以上三篇的具體論點來自 ResearchGate/Semantic Scholar 的摘要與搜尋結果彙整，我未能取得完整全文逐字核對。但「ACWR 遭到嚴重方法學挑戰」這個總體結論在文獻中一致且明確，不存在爭議。）**

**對本專案的意涵**：ACWR 的核心問題（數學耦合）本專案同樣會遇到——如果改用 `TSB/CTL`，由於 `TSB = CTL − ATL`，則 `TSB/CTL = 1 − ATL/CTL`，這**就是** ACWR 的一個單調變換，會繼承完全相同的數學耦合問題。所以「改成比值」並不能靠換公式解決，只是把問題換個樣子。

不過有一點要區分清楚：ACWR 被駁斥的是「**用它預測受傷風險**」這個主張。本專案 TSB 的用途是「標記今天可能狀態不佳，供 AI 教練參考」——這是**描述性**用途，不是流行病學因果推論，批判的殺傷力沒有那麼強。但也因此**不能宣稱有文獻背書**。

### 7/42 天時間常數是慣例還是有依據？

**是慣例。而且有同儕審查證據直接指出固定常數站不住腳。**

Hellard et al., *Assessing the limitations of the Banister model in monitoring training*（[PMC1974899](https://pmc.ncbi.nlm.nih.gov/articles/PMC1974899/)）：

- 個體擬合的時間常數**變異極大**：
  - **τa（體能衰減）：38 ± 16 天，範圍 13–65 天**
  - **τf（疲勞衰減）：19 ± 11 天，範圍 5–38 天**
- **所有參數的變異係數（CV）都超過 30%**
- 參數間存在嚴重的 **ill-conditioning**：τa 與 τf 的相關係數高達 **0.99 ± 0.01**，ka 與 kf 為 **0.91 ± 0.13**——這代表模型無法把兩者分開辨識，擬合出來的參數缺乏可解釋性
- 95% CI 極寬（tn 為 19 天，CI 7–35；tg 為 43 天，CI 25–61）
- 作者結論原文：**"proposing a single set of parameters might be dubious"**

其他限制：模型假設參數不隨時間變化（與實際觀察到的訓練反應時間依賴性矛盾）；把有氧、無氧、技術訓練混成單一刺激量「不切實際」。

**注意一個有趣的細節**：Hellard 擬合出的 τf（疲勞）中位數是 19 天，遠大於 TrainingPeaks 慣用的 7 天。TrainingPeaks 的 7/42 更接近一種**產品設計選擇**（讓圖表在視覺上好讀、讓 taper 週期對得上比賽準備），而非從生理擬合推導出來的值。

---

## 主題四：HRV 下降閾值

### 文獻的標準做法

HRV 引導訓練的方法學主線是 **Kiviniemi → Plews / Buchheit** 這一系。標準做法有三個要素：

**1. 對數轉換**：rMSSD 分布右偏，文獻一律先取自然對數成 **LnrMSSD** 再分析。

**2. 7 日滾動平均**：Plews 推廣用 7 日滾動平均（LnrMSSD₇d）過濾單日雜訊。單日值對睡眠、酒精、測量姿勢、前一日課表極度敏感，直接用單日值判讀會產生大量偽陽性。

**3. 個人化正常範圍（SWC）**：以個人基線的 **mean ± 0.5 × SD**（或 0.5 × CV）作為「正常範圍」界線。落在範圍內 → 照常安排中／高強度；落在範圍外 → 改低強度或休息。

Kiviniemi et al. 的原始 RCT 與後續複製研究（如 [HRV-Guided Training for Professional Endurance Athletes 協定，PMC7432021](https://pmc.ncbi.nlm.nih.gov/articles/PMC7432021/)）都採這個結構。該協定的具體參數：

- 範圍 = **LnrMSSD₇d 的 mean ± 0.5 × SD**
- **前 4 週**為標準化期，用以建立基線
- 正常範圍**每週更新**（不是固定的）
- **每日**晨起測量，每次 60 秒

Plews 另有一個重要發現：**LnrMSSD 的 CV（變異係數）本身**是有用訊號——非功能性過度努力（non-functional overreaching）的運動員身上，7 日滾動平均的 CV 呈現大幅線性下降（「日間變異塌縮、異常穩定」），對照組則穩定。也就是說，**HRV 變得太穩定也是壞訊號**，不只是變低。

### 百分比形式對不對？

**不對，而且對數轉換這件事本身就推翻了它。**

本專案目前算的是：

```python
drop_pct = (hrv_weekly_avg_ms - hrv_ms) / hrv_weekly_avg_ms   # >15% 即觸發
```

三個問題：

1. **在原始 ms 尺度上算百分比，而文獻在對數尺度上做判斷。** 對數尺度上的固定差值 ≙ 原始尺度上的固定比值，所以「百分比下降」在概念上其實比較接近對數尺度的思維——但本專案的百分比是拿**單日值**比**7 日均值**，而文獻是拿**7 日均值**比**基線分布**。層級不同。

2. **15% 是固定值，文獻要求個人化。** SWC 的整個要點就是「界線由**這個人自己的**變異度決定」。一個 HRV 天生波動大的人，15% 的日常波動可能完全正常；波動小的人，8% 就已經是明確訊號。用一個固定百分比套所有人，等於放棄了 SWC 的核心思想。

3. **比較對象不同。** 本專案是「單日 vs 7 日均值」，文獻是「7 日均值 vs 個人基線範圍」。前者對單日雜訊敏感得多——而滾動平均存在的**目的**就是消除那個雜訊。本專案的寫法等於把雜訊源直接當成訊號源。

**我未能找到任何同儕審查文獻使用「單日 HRV 相對 7 日均值下降 15%」這個判準。** 15% 這個數字在本專案的來源是實務經驗（程式碼註解自陳「取 15% 作為常見訓練監控實務中的保守分界」），**沒有文獻依據**。

### 基線需要多少資料？

- **建立基線**：實務協定用 **4 週**標準化期（PMC7432021）。
- **測量頻率**：Plews et al., *Monitoring Training With Heart-Rate Variability: How Much Compliance Is Needed for Valid Assessment?*（Int J Sports Physiol Perform）指出，**每週至少 3 個有效測量點**才足以做可靠的週度評估；每日測量更佳。
  （查證限制：僅取得該文的二手轉述與 ResearchGate 摘要頁，未能核對原文的完整統計條件。）

**對本專案**：292 天的資料量對建立個人基線是**充足的**（遠超 4 週）。**真正的關卡是測量頻率**——如果 HRV 資料不是每週至少 3 天，那麼 7 日滾動平均會建立在 1–2 個點上，其穩定性遠不如文獻假設，SWC 也算不準。這一點程式碼目前沒有檢查。

---

## 對本專案的具體建議

### 標示說明

- 🟢 **文獻推導**：有同儕審查來源直接支持
- 🟡 **實務權威**：來自 practitioner 來源（TrainingPeaks/Friel/Daniels），非同儕審查
- 🔴 **工程決策**：沒有文獻背書，是我們自己選的數字，未來須用自己的資料校正

---

### 1. 心率漂移

**建議：不設「異常」閾值，維持現狀只呈現數值。**

`fit_parser.compute_hr_drift()` 目前的註解寫「只回傳數值不做解讀」，這個設計**是對的，應該保留**。

若之後要加入判讀，順序應為：

1. 🟢 **先加前置條件檢查**：時長 < 45 分鐘、或配速變異過大（間歇/法特雷克）、或非 easy/aerobic 強度 → 直接回 `available: False`，不輸出漂移值。理由：指標本身假設前後半外部負荷相同，不滿足時數字無意義。（下限 45 分鐘本身是 🔴 工程決策；「必須穩定負荷」則是 🟢 指標定義的必要條件。）
2. 🔴 **改算 Pa:HR** 而非純心率差，並加入坡度校正（GAP）。在此之前，任何閾值都不成立。
3. 🔴 **閾值用自己的資料定**：對本專案已有的長跑資料算漂移分布，取個人的 P75/P90 當「偏高」線，而不是搬 5%。理由：正常長跑本來就會漂移（thermoneutral 下 8–12%），固定小閾值必然大量偽陽性。
4. 🟡 熱／濕環境下的活動應標記為「讀數不可信」而非判定異常。

**明確不要做的事**：不要把 5% 寫進程式碼當觸發線。它既是教練經驗法則（非科學），又是為不同的指標（Pa:HR）定義的。

---

### 2. 配速執行容差

**建議：依課表類型分開處理，並且不要用單一數字。**

| 課表類型 | 建議判定 | 標示 |
|---|---|---|
| Easy / Long | 用**區間**判定，不設點目標。落在 E 區間內 = 達成。**只在偏快時提示**（easy 跑太快是真問題），偏慢不提示 | 🟡 Daniels 的 E 本來就是區間 |
| Threshold | ±3%，雙向 | 🔴 工程決策 |
| Interval / Rep | 偏慢 −3% 提示；**偏快不提示為「未執行」但可提示「超速無額外效益」** | 🟡 不對稱邏輯來自 Daniels；±3% 數字是 🔴 |

**環境修正**：🟡 建議實作 Daniels/VDOT 的氣溫＋露點規則作為**判定前的預處理**：

```
adjustment_pct = max(0, (temp_F + dewpoint_F - 130) / 10) × 1%
adjusted_target_pace = target_pace × (1 + adjustment_pct)
```

台灣的氣候條件會讓這條規則在一年中大半時間都觸發，**不做修正的話夏季課表會被系統性判為未執行**。但係數本身是 practitioner 來源，且 Ely et al. 2007 顯示**較慢的跑者受罰更重**，所以套用單一係數會低估一般跑者受到的影響——🔴 建議累積本專案自己的資料後重新校正這個係數。

**誠實聲明**：±3% 這個數字**沒有任何文獻依據**，是純粹的工程決策。任何宣稱「文獻說配速偏差 X% 算沒執行」的說法都是編造的——這類文獻不存在。

---

### 3. TSB 閾值

**建議：立刻停用 −30。改用個人化的分布式門檻。**

**尺度依賴假設：確認成立。** TrainingPeaks 的數字綁在 TSS 尺度上，本專案的 `duration_sec × %HRR` 尺度大約 30 倍，直接搬運在數學上就是錯的。實測 55% 觸發率是這個錯誤的直接後果。

具體做法，依推薦程度排序：

**方案 A（推薦）🔴 個人分布式門檻**

```
以該運動員自己的歷史 TSB 分布定門檻，例如 TSB < P10（最低的 10% 天數）
```

- 優點：完全免疫尺度問題；自動個人化；觸發率可控（設 P10 就是 10% 的天數觸發，符合「異常應該罕見」的直覺）
- 缺點：需要足夠歷史資料（292 天足夠）；是純工程決策，**無文獻背書**
- 但誠實地說：TrainingPeaks 的 −30 同樣無文獻背書，方案 A 至少觸發率是可控的

**方案 B 🟡 尺度換算**

```
TSB_threshold = -30 × (本專案一小時 threshold 負荷 / 100)
              ≈ -30 × 30.6 ≈ -900
```

- 優點：保留與 TrainingPeaks 慣例的可比性
- 缺點：實測 TSB 最低只到 −669，這條線**永遠不會觸發**——反而暴露了本專案的 TSB 動態範圍與 TrainingPeaks 不同構，單純換算解決不了問題
- 換算比例本身還需個人化（依 HRmax/RHR 而異）

**方案 C（不推薦）ACWR 式比值**

- `TSB/CTL = 1 − ATL/CTL`，是 ACWR 的單調變換，會**繼承完全相同的數學耦合缺陷**
- ACWR 已被 Impellizzeri et al. 系統性駁斥，多篇文獻建議放棄
- 若仍要用，**絕不可宣稱有文獻支持**，且不可用於「受傷風險」這類因果宣稱

**額外建議 🟢 為 7/42 天時間常數加註**：Hellard et al. 顯示個體時間常數 CV > 30%（τa 13–65 天、τf 5–38 天），作者明言單一組參數「可疑」。`training_load.py` 目前的註解寫「沿用運動科學界廣泛驗證的標準值」——**這個描述不準確，建議修正**為「產業慣例值，個體差異大，文獻不支持單一組固定參數」。

---

### 4. HRV 下降閾值

**建議：把 15% 百分比換成 LnrMSSD 的 SWC。**

🟢 具體改法（全部有文獻支持）：

```
1. hrv_ln = ln(rMSSD)                      # 文獻一律先取對數
2. baseline_mean, baseline_sd = 過去 4 週 hrv_ln 的平均與標準差
3. swc_lower = baseline_mean - 0.5 × baseline_sd
4. 觸發條件：當日（或 7 日滾動平均）hrv_ln < swc_lower
5. baseline 每週更新
```

🟢 **同時加入資料充足度檢查**：若過去 7 天有效 HRV 測量點 < 3，該日 HRV 維度應標記為**資料不足**（沿用 1C 既有的資料完整度標記機制），而非用不可靠的均值硬算。這是目前 `_evaluate_hrv()` 缺少的檢查——它只檢查 `hrv_ms` 與 `hrv_weekly_avg_ms` 是否為 None，沒有檢查那個週均值是由幾個點算出來的。

🟢 **修正既有的信賴度標記**：`readiness.py` 的 `_evaluate_hrv()` 目前註解說「7 日均值不需要長歷史即可成立，故標 high」。這個判斷**與文獻不符**——文獻要求 4 週基線期才建立正常範圍，且要求每週 ≥3 個測量點。建議把信賴度改為依實際測量點數決定。

🟡 **可選加分項**：Plews 指出 LnrMSSD 的 **CV 塌縮**（日間變異異常變小）是非功能性過度努力的早期訊號。這是現有實作完全沒有捕捉的維度，且方向與直覺相反（HRV「太穩定」也是壞事）。

**誠實聲明**：現行的 15% 🔴 沒有文獻依據。程式碼註解自陳「取 15% 作為常見訓練監控實務中的保守分界」——這個描述算誠實，但應進一步標明它不是從 HRV 文獻來的。

---

## 來源清單

### Peer-reviewed

| 來源 | URL | 用於 |
|---|---|---|
| Coyle EF, González-Alonso J. *Cardiovascular drift during prolonged exercise: new perspectives.* Exerc Sport Sci Rev, 2001 | https://pubmed.ncbi.nlm.nih.gov/11337829/ | 主題一：心率漂移為正常生理現象、機制 |
| Ely ML, Cheuvront SN, Roberts WO, Montain SJ. *Impact of weather on marathon-running performance.* Med Sci Sports Exerc, 2007 | https://pubmed.ncbi.nlm.nih.gov/17473775/ | 主題二：WBGT 與馬拉松成績、慢跑者受罰更重 |
| Ely MR et al. *Marathon performance in thermally stressing conditions.* Sports Med, 2007 | https://pubmed.ncbi.nlm.nih.gov/17465598/ | 主題二：熱環境下馬拉松表現 |
| Gabbett TJ. *The training-injury prevention paradox.* Br J Sports Med, 2016 | https://pubmed.ncbi.nlm.nih.gov/26758673/ ／ https://efsma.org/images/pdf/publications/Br-J-Sports-Med-2016-Gabbett-273-80.pdf | 主題三：ACWR 原始主張、sweet spot 0.8–1.3 |
| Impellizzeri FM, Tenan MS et al. *Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls.* 2020 | https://www.semanticscholar.org/paper/ede5743a426fd6429d28f8505500a3f771dbcf8b | 主題三：數學耦合、無證據支持實務應用 |
| *The acute-chronic workload ratio-injury figure and its sweet spot are flawed.* 2019 | https://www.researchgate.net/publication/333589357 | 主題三：要求撤稿 sweet spot 圖表 |
| *What Role Do Chronic Workloads Play in the ACWR? Time to Dismiss ACWR and Its Underlying Theory.* 2020 | https://www.researchgate.net/publication/347442902 | 主題三：建議放棄 ACWR |
| Hellard P et al. *Assessing the limitations of the Banister model in monitoring training.* J Sports Sci | https://pmc.ncbi.nlm.nih.gov/articles/PMC1974899/ | 主題三：時間常數個體差異、ill-conditioning、"single set of parameters might be dubious" |
| *HRV-Guided Training for Professional Endurance Athletes: A Protocol for a Cluster-Randomized Controlled Trial.* IJERPH 2020 | https://pmc.ncbi.nlm.nih.gov/articles/PMC7432021/ | 主題四：SWC = mean ± 0.5×SD on LnrMSSD₇d、4 週基線、每週更新 |
| Plews DJ et al. *HRV in elite triathletes: is variation in variability the key?* | https://www.researchgate.net/publication/221863314 | 主題四：CV 塌縮為過度努力訊號 |
| Plews DJ et al. *Monitoring Training With HRV: How Much Compliance Is Needed for Valid Assessment?* IJSPP | https://www.researchgate.net/publication/259319333 | 主題四：每週至少 3 個測量點 |
| *HRV-guided training in professional runners: Effects on performance and vagal modulation.* Physiol Behav 2021 | https://www.sciencedirect.com/science/article/abs/pii/S0031938421003413 | 主題四：HRV 引導 vs 傳統訓練 RCT |
| Montain SJ, Coyle EF (1992) 脫水與心血管漂移 | 未能取得直接連結 | 主題一：漂移與體重流失成正比、2% 門檻 |

### Practitioner-authority

| 來源 | URL | 用於 |
|---|---|---|
| TrainingPeaks / Joe Friel — Aerobic Endurance and Decoupling | https://www.trainingpeaks.com/coach-blog/aerobic-endurance-and-decoupling/ | 主題一：5%／5–10%／>10% 分級、建議適用時長 |
| TrainingPeaks Help — Aerobic Decoupling (Pw:Hr and Pa:HR) and EF | https://help.trainingpeaks.com/hc/en-us/articles/204071724 | 主題一：**HTTP 403，未能取得內容** |
| TrainingPeaks — A Coach's Guide to ATL, CTL & TSB | https://www.trainingpeaks.com/coach-blog/a-coachs-guide-to-atl-ctl-tsb/ | 主題三：TSB 分區 +15~+25／−10~−30／超過 −30 |
| TrainingPeaks Help — Form (TSB) | https://help.trainingpeaks.com/hc/en-us/articles/204071764-Form-TSB | 主題三：TSB 定義 |
| V.O2 / VDOT News — Adjust Your Training Paces For High Temperatures | https://news.vdoto2.com/2015/07/adjust-your-training-paces-for-high-temperatures/ | 主題二：Daniels 溫度修正 |
| RunnersConnect — Dew Point Effect on Running | https://runnersconnect.net/dew-point-effect-running/ | 主題二：露點 vs 相對濕度的理由 |
| RunnersConnect — Temperature Calculator | https://runnersconnect.net/training/tools/temperature-calculator/ | 主題二：溫度修正實作 |

### Secondary

| 來源 | URL | 用於 |
|---|---|---|
| Fellrnr — Jack Daniels | https://fellrnr.com/wiki/Jack_Daniels | 主題二：E/I/T 配速定義（二手轉述） |
| Coach Ray — Jack Daniels' Running Intensity | https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/ | 主題二：強度區間百分比 |
| Fellrnr — Modeling Human Performance | https://fellrnr.com/wiki/Modeling_Human_Performance | 主題三：Banister 模型限制概述 |
| Maximum Performance Running — Temperature + Dew Point | http://maximumperformancerunning.blogspot.com/2013/07/temperature-dew-point.html | 主題二：溫度＋露點規則 |
| Marathon Handbook — Cardiac Drift | https://marathonhandbook.com/cardiac-drift/ | 主題一：漂移幅度概述（**數字未能追溯原始論文**） |
| Global Performance Insights — Has ACWR Been Debunked? | https://www.globalperformanceinsights.com/post/has-the-acute-chronic-workload-ratio-been-debunked | 主題三：ACWR 爭議整理 |
| EliteHRV — Coefficient of Variation | https://elitehrv.com/improving-hrv-data-interpretation-coefficient-variation | 主題四：CV 在 HRV 判讀中的角色 |
| HRVtraining — HRV: Means and Variation | https://hrvtraining.com/2014/04/05/hrv-means-and-variation/ | 主題四：均值與變異度 |

---

## 查證限制

以下項目**未能完整查證**，引用時應保留懷疑：

1. **Coyle & González-Alonso (2001) 原文摘要未能逐字核對** —— PubMed 頁面因 cookie 牆返回無內容。本文引用的機制論述（stroke volume 下降、舒張期充填時間縮短）來自搜尋結果彙整。該結論廣為引用且無爭議，但未經原文核對。

2. **「正常長跑漂移 8–12%」的原始出處未能追溯** —— 此數字來自二手整理來源（marathonhandbook 等），未能找到對應的原始論文與其確切實驗條件。**這是本文中對建議影響最大、但證據最弱的一個數字**，若要據此否定 5% 閾值，建議先用本專案自己的資料驗證。

3. **Montain & Coyle (1992) 原文未取得** —— 「2% 體重流失門檻」「不補水心率上升 10%、補水 5%」均為二手轉述。

4. **TrainingPeaks Help Center 的 Pa:HR 官方定義未取得** —— 該頁面回 HTTP 403。Pa:HR 的確切計算公式（是配速÷心率還是心率÷配速、如何處理單位）**未能從官方來源核對**，本文的描述來自其教練部落格與第三方說明。

5. **Ely et al. 2007 的迴歸方程式係數未取得** —— 僅取得分區間（WBGT 四分位）的比較結果，**沒有可直接代入的連續修正公式**。想要「輸入溫濕度輸出修正配速」目前只能用 Daniels 的 practitioner 規則。

6. **《Daniels' Running Formula》原書未取得** —— 所有 Daniels 相關數字（E 的 ±20 sec/mile、訓練期程的 10/4 sec/mile 進程、I 的 95–100% VO₂max）**全部來自二手轉述**，未經原書核對。

7. **Daniels 溫度修正規則的精確係數在來源間有出入** —— 「130 起算、每 10 點慢 1%」是最常見的版本，但不同二手來源給的起算點與係數略有差異，且**未能找到 VDOT 官方演算法規格**。

8. **Impellizzeri et al. 三篇批判文的全文未取得** —— 論點來自 ResearchGate/Semantic Scholar 摘要與搜尋結果。「ACWR 遭嚴重方法學挑戰」的總體結論明確無爭議，但個別論證細節未經逐字核對。

9. **Plews 的 compliance 論文全文未取得** —— 「每週至少 3 個測量點」來自二手轉述與摘要頁，未核對其完整統計條件（在何種前提下、多大誤差容忍度下成立）。

10. **本專案負荷尺度與 TSS 的 30 倍換算是我自己推算的**，不是文獻數字。推算依據：`3600 sec × 0.85 HRR ≈ 3060` 對應 TSS 100。**實際比例會隨個人 HRmax/RHR 與強度分布而變**，這個數字應視為數量級估計，不是精確換算因子。但「尺度不是 1，且相差一個數量級以上」這個結論是穩固的。

11. **未查證**：是否有專門針對「跑步 raw HR drift（無配速分母）」建立閾值的文獻。我的搜尋沒有找到，但不能排除存在。本文據此建議「不設閾值」，屬於「查無支持」而非「查得反證」。
