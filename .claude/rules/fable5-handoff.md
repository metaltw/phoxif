# 給未來 Session 的交接信(phoxif,Fable 5)

2026-07-07 初版(開釋 session);2026-07-15 增補(設計 session 後現況,見文末)。
寫給接手本 repo 的 Sonnet/Opus/Haiku session。制度檔(ADR/roadmap/quality/
TODO)說「做什麼、怎麼驗」;這封信說制度沒寫、但你遲早會撞到的東西。

## 一、使用者沒問、但對此專案最關鍵的三件事

**1. 這是資料救援專案,不是照片管理專案。**
差別在於:管理的對象可以重來,救援的對象不行。原始 mtime、原始路徑、
原始檔名是「案發現場」,任何複製/移動/改寫之前沒記錄下來,就永遠消失。
所以 ADR-0001 要求 Ingest 先登記證據、ADR-0002 的證據欄位唯讀、
ADR-0006 要求寫入前記原值。你每次想「先動手再說」,想想現場保全。

**2. 錯誤極性不對稱,是整套 metadata 決策的底層邏輯。**
日期錯 = timeline 偏移,有標記可修 → 盡量填(ADR-0004);
GPS 錯 = 地圖上可信的謊言 → 寧缺勿錯(ADR-0005);
刪錯檔 = 不可逆 → 永遠 send2trash + 驗證閘。
遇到 ADR 沒覆蓋的新決策,先問「這個錯了之後,使用者發現得了嗎?修得回嗎?」
用答案決定寬鬆度,不要憑對稱直覺把三者用同一套標準。

**3. 最高價值的功能藏在去重裡,不在補值裡。**
「WeChat 檔案補日期」聽起來是明星功能,但對每一張「原檔還在收藏某處」的
轉存照片,正解是 phash 配對後**丟掉轉存檔**,而不是給它估日期。
補值是去重的殘差處理。誰先誰後(ADR-0001)不是流程潔癖,是產出品質的分水嶺。

## 二、本專案在弱模型長期開發下最可能的退化方式

| 退化模式 | 徵兆 | 預防/處置 |
|---|---|---|
| GUI 功能蔓生、管線沒人做 | 連續多個 commit 都在加畫面細節(歷史前科:8 個 commit 有 5 個在做 orientation) | 開工先對 roadmap:目前 Phase 是什麼,這個任務在哪格;不在格子裡 → 記 TODO 不動手 |
| 安全違規繁殖 | 新 code 模仿舊版 `-overwrite_original` 例子 | quality.md G1 閘門每輪跑；以該檔當前基線為準，任何 API 新增命中 = FAIL |
| 測試骨架荒廢 | 新功能 PR 無新測試、測試數長期不變 | DoD 表(quality.md §3)第一行就是先紅後綠；目前基線 157 tests |
| catalog schema 隨手改 | 「加個欄位而已」不寫 migration | ADR-0002 紀律:動 schema = migration + user_version + ADR 增補 |
| 溯源標記被「順手簡化」 | 「keyword 好像沒人用,先拿掉」 | 標記是可逆性的全部;拿掉 = 違反 ADR-0004 底線,一票 FAIL |
| 個資滲漏 | 測試/文件裡出現真實路徑、座標、主機名 | 個資閘門 grep(quality.md §1)進收工流程;公開 repo 無法撤回歷史 |

## 三、本次產出的誠實信心評估

| 產出 | 信心 | 原因 |
|---|---|---|
| ADR-0001/0002/0006/0007(管線、catalog、安全、legacy) | 高 | 依據 code 實證 + 通用工程原理 |
| ADR-0003(Immich external library) | 高 | 與既有 production deployment 對齊；「改名斷關聯」機制本身未本地實測,屬官方已知行為 |
| ADR-0004 信心階梯的**結構** | 高 | 極性已由使用者拍板,階梯機器可測 |
| ADR-0004 各 heuristic 的**細節** | 中 | mmexport 13 位 ms epoch、LINE 檔名格式等來自訓練知識+多源一致,**未在使用者的真實檔案上驗證**——Phase 3 開工第一件事:抽 30 個真實轉存檔核對檔名格式假設 |
| Immich 會索引 IPTC/XMP keywords 成可搜尋 tag | 中低 | **未實測**。Phase 3 驗收含此項:寫一張測試照進 Immich 搜 `phoxif:date-estimated`;搜不到 → 觸發 ADR-0004 重估訊號(改載體,階梯不變) |
| 2026-07-15 當時的 tests/ 22 cases | 高 | 歷史快照；2026-07-18 已增至 157 tests，不得把 22 當現況 |
| roadmap 規模估計(S/M/L) | 低 | 工程判斷,未經此 repo 實戰校準 |

## 四、2026-07-15 當時的未完成項（已被 2026-07-18 增補取代）

1. ~~reports/ 的 HTML 圖解版品質~~——已解決:兩份報告(0707 診斷、
   0714 設計實證)皆已手寫圖解版,HTML 檔頭有「勿用 md2report 覆蓋」註記。
2. `docs/design.md` 與 `docs/workflow.md` 未整併:兩檔含過時內容
  (workflow.md 的 shell 配方違反自家安全規則)。已在 CLAUDE.md 路由表
   標「與 ADR 矛盾時以 ADR 為準」,正式改寫是 P1-9 之後的事。
3. ~~當時尚未做 P0 修復。~~ **已完成；不得依此舊狀態重做。**
4. `CLAUDE.md.bak`(repo 根,未追蹤)待使用者核准後刪除。

## 五、2026-07-15 的舊啟動 prompt（已作廢）

不要再執行本節舊 prompt。接手方式以文末「2026-07-18 交接增補」為準。

---

## 2026-07-15 交接增補:手邊工作現況

### 已完成(全部已 commit + push,HEAD 56ea8a1)

- **詳細設計正本 `docs/pipeline-design.md`**:雙模式(rescue+inbox)管線、
  catalog DDL(sqlite3 實跑驗證)、模組介面簽名、自動化政策矩陣(§10,
  authoritative)、連拍判別與不對稱證據公式(§6)、領域地雷(§6.1:
  Live Photo 配對、iOS 編輯變體、mtime 還原、ASCII 歸檔路徑、catalog 備份)。
  經兩輪 fresh-context 對抗審查,3 個 MUST-FIX 全修復。
- **ADR-0008**(雙模式與自動化粒度)+ roadmap Phase 1/2 校準
  (census 併入 Phase 1)+ CLAUDE.md 路由與 Current State 更新。
- **實證報告** `reports/20260714-pipeline-design-evidence.md/.html`:
  1117 檔只讀普查,推翻兩個預設——(a) near-dup d≤4 幾乎全是連拍,
  自動判重需不對稱證據;(b) 影片日期在 QuickTime:CreateDate(MP4 的
  DTO 僅 0.1%)。mtime 可信度實測 97.5–99.6%(第 6 級 fallback 啟用,
  逐來源重評)。

### 進行中 / 懸而未決

- **等使用者拍板**(pipeline-design.md §13,不阻塞 P0/P1):
  (1) 歸檔樹佈局(建議日期樹;他現行習慣是位置資料夾,若要位置樹需修
  ADR-0003)(2) staging 位置(建議外接 SSD)(3) inbox intake 資料夾清單。
- **mmexport/LINE 檔名格式仍未實證**(本機零樣本)——設計已改為
  「每來源普查先行、解析器實證後才啟用」;使用者若提供其他電腦的
  WeChat 樣本包,優先做解析器校準。
- P1-9(design.md 措辭更新)依規定要先給使用者看 diff 才能動。

### 2026-07-15 當時的實作順序（已完成，不得重跑）

P0-1 → P0-2 → P0-3 → P0-4 → Phase 1 已完成。這段只保留決策歷史；
接手者必須從文末 2026-07-18 增補開始，不得重跑這些階段。§10 政策矩陣
仍是唯一 authoritative 表；§6 連拍判別先於自動判重的約束仍有效。

### 本 session 踩過的坑(避免重踩)

- 跨 repo 跑 git 指令前先確認 cwd；過去曾在錯誤 repo 執行 push。
  這是操作紀律提醒，與 phoxif 歷史無關。
- Subagent 的 Write tool 禁建名為 report/summary 的檔案——派普查類
  任務時,要求「結果以訊息回傳 + 原始數據落 scratchpad」,不要指定
  它寫 report.md。

---

## 2026-07-18 交接增補:GUI 阻斷修復後現況

### 使用者的直接回饋（必須視為產品驗收紅線）

使用者實際操作新版網頁後判定「比大改版以前還爛」，原因不是視覺風格，而是
三個核心操作契約失效：

1. 從頭到尾看不到任何照片縮圖。
2. 無法辨認哪個元件是選項、哪個按鈕會進到下一步。
3. 貼入 photo folder path 後，不知道路徑是否加入、是否已掃描、現在發生什麼。

未來任何 GUI 改版只要重現其中一項，就算測試全綠也不得宣稱完成。

### 根因與修正（commit `8212564`）

- 根因：後端 `/api/thumbnail` 已存在，但前端 `scanSources()` 把 `data.files`
  丟掉；一般掃描結果沒有任何照片 DOM，只有 dedupe 人工 review 才偶爾渲染圖。
- `ScanResult` 現在保留完整 `files`，結果頁固定顯示「phoxif 實際讀到的照片」。
- 首批只渲染 24 個媒體檔；「再載入」每次增加 24，禁止恢復無界限
  `map(all files)`，否則數千張照片會凍結 GUI。
- 縮圖載入失敗時顯示「無法預覽／檔案仍完整保留」，不可只留 broken image。
- 路徑加入後明示「已加入 N 個照片資料夾／尚未掃描／等待掃描」，主按鈕改為
  「開始掃描 N 個資料夾」。
- 模式卡明示「目前選擇／選擇此模式」；掃描結果首屏直接顯示「你現在在哪裡」
  與唯一主要 CTA「建立安全工作副本」。

### 已驗證證據

- `uv run pytest -q`：157 passed。
- `uv run ruff check .`：All checks passed。
- `cd frontend && npm run build`：TypeScript + Vite build pass。
- `docs/quality.md` G3 個資閘門：無輸出。
- Browser end-to-end：貼入 `/tmp/phoxif-ui-demo` → 顯示已加入/等待掃描 →
  開始掃描 → 3/3 PNG 縮圖成功，naturalWidth 皆為 1024 → 首屏唯一下一步
  CTA 可見。
- Fresh-context verifier 第一輪提出 CTA、無界限 DOM、縮圖 fallback 三項 P1；
  修正後複查為「無剩餘阻斷 findings」。

### Fable 5 接手方式

第一件事不是再畫 mockup，也不是重做 Phase 0/Phase 1；請直接用使用者指定的
真實照片資料夾做一次完整 operator walkthrough，逐步回答：

1. 畫面上是否立即看得到實際照片，而不只是統計數字？
2. 每一頁是否只有一個明顯的主要下一步？
3. 使用者是否隨時知道來源路徑、目前狀態、已做與未做的動作？
4. HEIC、影片、損壞檔與大量照片時，fallback/分批載入是否仍清楚？

若 walkthrough 發現問題，優先修 operator visibility，不要先增加新功能。

### Git / 工作樹邊界

- HEAD 在本增補前為 `8212564`；該 commit 只含 5 個 frontend 檔。
- `reports/INDEX.md`、`reports/index.html` 與 product guide 是既存未提交工作；
  `.DS_Store`、`AGENTS.md`、`CLAUDE.md.bak` 等也是既存未追蹤檔。
- 不得把上述檔案混入 GUI/handoff commit，不得自行刪除。
