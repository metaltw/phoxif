# phoxif 管線設計實證報告(2026-07-14)

配合 `docs/pipeline-design.md`(詳細設計正本)與 ADR-0008 的證據基礎。
資料:對本機已整理工作區的**只讀**普查(n=1117 媒體檔;零寫入,
以 `find -newer` 錨點檔證明)。原始統計留在 session scratchpad,
本報告只含聚合數字。

## TL;DR

1. 實證推翻了兩個設計預設:**near-dup 自動判重會誤殺連拍**(已改:
   連拍判別 + 不對稱證據門檻);**影片日期欄位是 CreateDate 不是
   DateTimeOriginal**(MP4 的 DTO 存在率僅 0.1%)。
2. mtime 比防禦性假設可靠(≤1min 一致率 97.5–99.6%)——第 6 級
   fallback 啟用,但逐來源用普查重新評估。
3. WeChat/LINE 檔名格式假設**本機無樣本可驗**(mmexport/wx_camera/LINE
   全部 0 檔)——維持「每來源普查先行」的設計,解析器逐來源啟用。

## 一、Corpus 概況(n=1117)

| 維度 | 數字 |
|---|---|
| 型別 | MP4 89.4%、JPEG 9.2%、HEIC 1.3% |
| 頂層資料夾 | 58 夾;每夾 min 1 / median 6 / max 257 |
| 檔名 | 100% 符合 `YYYYMMDD_HHMMSS(_N)` (既有 rename 流程的產出側) |
| 影片 codec | 100% HEVC(既有 convert 流程已跑完) |
| GPS | 100%(既有 write_gps 已跑完) |

註:這是既有手動工作流的**產出側**樣本——檔名/GPS/HEVC 數字代表
「處理完會長什麼樣」,不代表雜亂輸入側;輸入側(其他機器上的
WeChat/LINE 檔)仍待逐來源普查。

## 二、EXIF 欄位覆蓋率 → 設計修正 1

| 型別 | n | DateTimeOriginal | CreateDate |
|---|---|---|---|
| MP4 | 999 | 0.1% | 100.0% |
| JPEG | 103 | 99.0% | 99.0% |
| HEIC | 15 | 100.0% | 100.0% |

**修正**:日期階梯第 1 級按媒體型別選欄位——影像 DTO-first、
影片 QuickTime:CreateDate(UTC,需 `-api QuickTimeUTC=1`)。
影像思維的 DTO-first 設計會漏掉本 corpus 九成檔案。
(pipeline-design.md §7 已更新)

## 三、near-dup 距離分布 → 設計修正 2(最重要)

phash 全量比對(n=118 影像,6903 對):

| Hamming 距離 | 配對數 | 主要成因 |
|---|---|---|
| ≤ 4 | 112 | **絕大多數是連拍/同景連按**(`_N` 尾碼叢集) |
| 5–8 | 200 | 同景變化 |
| 9–12 | 246 | 弱相似 |

**修正**:原設計「d≤4 自動判重」會把連拍當重複刪掉。新規則
(pipeline-design.md §6):先過連拍判別(同來源同夾、雙方原生 EXIF、
時間差 ≤10s → burst,雙留);自動判重另需**不對稱證據**(一方無原生
EXIF 或明顯再壓縮);對稱配對即使 d=0 也進人工佇列。

exact dup:1117 檔中僅 1 組(2 檔)——已整理區如預期乾淨。

## 四、mtime 可信度 → 第 6 級 fallback 校準

| 比對 | n | ≤1min | >30day |
|---|---|---|---|
| DTO vs FileModifyDate(影像) | 118 | 97.5% | 1.7% |
| CreateDate vs FileModifyDate(全型別) | 1116 | 99.6% | 0.4% |

啟用第 6 級(mtime fallback,最低信心、必標記);但此數字來自
本機管理的 corpus,**每個新來源以自己的普查數字重新評估**。

## 五、仍未驗證(誠實清單)

| 假設 | 狀態 | 驗證計畫 |
|---|---|---|
| mmexport 13 位 ms epoch 格式 | 本機 0 樣本,無法驗 | 每來源普查(census)先行;Phase 3 接第一個含 WeChat 檔的來源時實證 |
| LINE 檔名格式 | 同上 | 同上;解析器表**不預先加 LINE 列**,普查實證後才加 |
| Immich 對 IPTC/XMP keywords 的索引 | 未實測 | Phase 3 驗收項(一張測試照進 Immich 搜標記) |
| 影片(MOV/MP4)keyword 寫入被 Immich 讀取 | 未實測 | 同上,影像/影片各測一張 |

## 六、本次設計交付物

| 檔案 | 內容 |
|---|---|
| `docs/pipeline-design.md` | 詳細設計正本:雙模式、catalog DDL、模組介面、連拍判別、政策矩陣、效能驗算、Live Photo/編輯變體等領域地雷 |
| `docs/adr/0008-dual-mode-automation-policy.md` | 新決策:救援+進水口雙模式、自動化粒度 |
| `docs/roadmap.md` | Phase 1/2 依設計校準(census 併入 Phase 1) |
| 本報告 | 設計的證據基礎 |

## 七、待 Metal 拍板(不阻塞 P0/P1,詳 pipeline-design.md §13)

1. 歸檔樹維持日期佈局(建議)vs 位置佈局——你現行習慣是位置資料夾,
   建議位置資訊改由 GPS + 可選 keyword 承載,樹用日期(穩定、免碰撞、
   跨系統 ASCII 安全)。
2. staging 放本機或外接 SSD(config 化,建議外接)。
3. inbox intake 資料夾清單(Phase 1 實作時定)。
