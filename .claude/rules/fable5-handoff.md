# 給未來 Session 的交接信(phoxif,Fable 5)

2026-07-07 初版(開釋 session);2026-07-15 增補(設計 session 後現況,見文末)。
寫給接手本 repo 的 Sonnet/Opus/Haiku session。制度檔(ADR/roadmap/quality/
TODO)說「做什麼、怎麼驗」;這封信說制度沒寫、但你遲早會撞到的東西。

## 一、Metal 沒問、但對此專案最關鍵的三件事

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
| 安全違規繁殖 | 新 code 模仿 actions.py 的 `-overwrite_original` 舊例 | quality.md G1 閘門每輪跑;基線 8 處(api 4=P0-4、legacy 4=ADR-0007)修掉前,任何**新增**命中 = FAIL |
| 測試骨架荒廢 | 新功能 PR 無新測試、22 這個數字很久沒變 | DoD 表(quality.md §3)第一行就是先紅後綠;verifier 檢查測試數有沒有隨功能長 |
| catalog schema 隨手改 | 「加個欄位而已」不寫 migration | ADR-0002 紀律:動 schema = migration + user_version + ADR 增補 |
| 溯源標記被「順手簡化」 | 「keyword 好像沒人用,先拿掉」 | 標記是可逆性的全部;拿掉 = 違反 ADR-0004 底線,一票 FAIL |
| 個資滲漏 | 測試/文件裡出現真實路徑、座標、主機名 | 個資閘門 grep(quality.md §1)進收工流程;公開 repo 無法撤回歷史 |

## 三、本次產出的誠實信心評估

| 產出 | 信心 | 原因 |
|---|---|---|
| ADR-0001/0002/0006/0007(管線、catalog、安全、legacy) | 高 | 依據 code 實證 + 通用工程原理 |
| ADR-0003(Immich external library) | 高 | 與既有生產環境對齊(私有維運工具 immich_cleaner 實證 external library 在用);「改名斷關聯」機制本身未本地實測,屬官方已知行為 |
| ADR-0004 信心階梯的**結構** | 高 | 極性是 Metal 拍板,階梯機器可測 |
| ADR-0004 各 heuristic 的**細節** | 中 | mmexport 13 位 ms epoch、LINE 檔名格式等來自訓練知識+多源一致,**未在 Metal 的真實檔案上驗證**——Phase 3 開工第一件事:抽 30 個真實轉存檔核對檔名格式假設 |
| Immich 會索引 IPTC/XMP keywords 成可搜尋 tag | 中低 | **未實測**。Phase 3 驗收含此項:寫一張測試照進 Immich 搜 `phoxif:date-estimated`;搜不到 → 觸發 ADR-0004 重估訊號(改載體,階梯不變) |
| tests/ 22 cases | 高 | 實跑全綠;但它們鎖的是「現行行為」,其中三個行為本身可能是 bug(見 tests 註記與 TODO) |
| roadmap 規模估計(S/M/L) | 低 | 工程判斷,未經此 repo 實戰校準 |

## 四、未完成項(2026-07-15 更新)

1. ~~reports/ 的 HTML 圖解版品質~~——已解決:兩份報告(0707 診斷、
   0714 設計實證)皆已手寫圖解版,HTML 檔頭有「勿用 md2report 覆蓋」註記。
2. `docs/design.md` 與 `docs/workflow.md` 未整併:兩檔含過時內容
  (workflow.md 的 shell 配方違反自家安全規則)。已在 CLAUDE.md 路由表
   標「與 ADR 矛盾時以 ADR 為準」,正式改寫是 P1-9 之後的事。
3. 沒有做任何 P0 修復(兩次 session 都是制度/設計,未施工)——P0-1
   (convert.py 刪原始影片)仍是接手 session 該做的第一件事。
4. `CLAUDE.md.bak`(repo 根,未追蹤)待 Metal 核准後刪除。

## 五、下一個 session 怎麼開始(建議的第一句 prompt)

```
讀 CLAUDE.md 路由表與 TODO.md,執行 P0-1(convert.py 安全閘),
依 docs/quality.md 的 DoD 驗收,完成後更新 TODO 已完成段。
```

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

- **等 Metal 拍板**(pipeline-design.md §13,不阻塞 P0/P1):
  (1) 歸檔樹佈局(建議日期樹;他現行習慣是位置資料夾,若要位置樹需修
  ADR-0003)(2) staging 位置(建議外接 SSD)(3) inbox intake 資料夾清單。
- **mmexport/LINE 檔名格式仍未實證**(本機零樣本)——設計已改為
  「每來源普查先行、解析器實證後才啟用」;Metal 若提供其他電腦的
  WeChat 樣本包,優先做解析器校準。
- P1-9(design.md 措辭更新)依規定要先給 Metal 看 diff 才能動。

### 實作順序(定案,勿重排)

P0-1 → P0-2 → P0-3 → P0-4(TODO.md,安全止血)→ Phase 1 照
pipeline-design.md §3(DDL 原文)+ §5(census/ingest)實作,
驗收條件在 roadmap.md Phase 1。實作時注意:§10 政策矩陣是唯一
authoritative 表;§6 連拍判別先於自動判重,對稱配對(含 d=0)不得自動刪。

### 本 session 踩過的坑(避免重踩)

- 跨 repo 跑 git 指令前先確認 cwd:07-14 曾在 war_room 誤跑
  `git push`,把一個既有本地 commit(d432cf3,與 phoxif 無關)推上
  origin。已如實回報 Metal;Metal 未表示需要處理。
- Subagent 的 Write tool 禁建名為 report/summary 的檔案——派普查類
  任務時,要求「結果以訊息回傳 + 原始數據落 scratchpad」,不要指定
  它寫 report.md。
