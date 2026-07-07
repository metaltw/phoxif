# 給未來 Session 的交接信(phoxif,Fable 5,2026-07-07)

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

## 四、未完成項(本 session 明知未做)

1. reports/ 的 HTML 圖解版品質:md2report 模板轉換是底線,重要報告
   (診斷書)值得手寫圖解版——本次用轉換版,夠用但不精緻。
2. `docs/design.md` 與 `docs/workflow.md` 未整併:兩檔含過時內容
  (workflow.md 的 shell 配方違反自家安全規則)。已在 CLAUDE.md 路由表
   標「與 ADR 矛盾時以 ADR 為準」,正式改寫是 P1-9 之後的事。
3. 沒有做任何 P0 修復(模板規則:只立制度不施工)——P0-1(convert.py
   刪原始影片)是接手 session 該做的第一件事。

## 五、明天怎麼開始(建議的第一句 prompt)

```
讀 CLAUDE.md 路由表與 TODO.md,執行 P0-1(convert.py 安全閘),
依 docs/quality.md 的 DoD 驗收,完成後更新 TODO 已完成段。
```
