# ADR-0004:日期補齊 = 盡量填 + 可逆標記 + 信心階梯

狀態:Accepted(2026-07-07,Metal 訪談定案「盡量填+標記估計」)

## 背景

WeChat/LINE 轉存檔的 EXIF 被剝除,檔名時間戳是「存檔/收到時間」,
不是拍攝時間。沒有日期的照片在 Immich timeline 會沉到匯入日,等於沒整理。
Metal 拍板:寧可寫入近似日期讓 timeline 正確,但必須可辨識、可回溯修正。

## 決策

### 寫什麼

- 估計日期寫入標準欄位:圖片 `EXIF:DateTimeOriginal` + `EXIF:CreateDate`;
  影片 `QuickTime:CreateDate`。這樣 Immich/任何工具都直接可用。
- **同時**寫入溯源標記(兩者都寫,最大化下游相容):
  - `IPTC:Keywords` 與 `XMP-dc:Subject` 加值:
    `phoxif:date-estimated`、`phoxif:date-src:<heuristic>`
    (heuristic ∈ filename-epoch / filename-date / batch-interp / folder-name / ingest-mtime)
  - 精度不足時再加 `phoxif:date-precision:<day|month|year>`
- MP4/MOV 容器不支援 legacy IPTC IIM;影片以 `XMP-dc:Subject` 保存同一組
  provenance keywords，圖片仍同時寫 IPTC 與 XMP。影片必須有整合測試證明
  QuickTime 日期與 XMP 標記都能 read-back。
- catalog(ADR-0002)記:寫入值、heuristic、寫入前原值。
- 已有**可信原生 EXIF 日期的檔案一律不碰**(第 1 級,不寫不標)。

### 信心階梯(由高到低,取第一個命中且通過 sanity 檢查的)

| 級 | 來源 | 語意 | 處置 |
|---|---|---|---|
| 1 | 原生 EXIF DateTimeOriginal/CreateDate 且值合理 | 拍攝時間 | 不動 |
| 2 | 檔名完整時間戳 `YYYYMMDD_HHMMSS`、`IMG_20230405_120000` 等 | 多為相機命名=拍攝時間 | 寫入,src:filename-date |
| 3 | `mmexport<13位ms epoch>`、`wx_camera_<epoch>` 等 | 存到相簿的時間=拍攝時間的**上界** | 寫入,src:filename-epoch,標 estimated |
| 4 | 同批鄰檔內插(同資料夾/同 batch、前後檔案日期夾擠) | 收到批次的時間帶 | 寫入,src:batch-interp,標 estimated |
| 5 | 資料夾名含日期(`2019-04 東京`、`2021 家族旅遊`) | 人工整理留下的線索 | 寫入,src:folder-name,標 estimated + precision |
| 6 | ingest 時記錄的來源 mtime(ADR-0001 證據欄) | 最後修改時間,搬運可能已污染 | 寫入,src:ingest-mtime,標 estimated |
| — | 全部落空 | — | 不寫,status=quarantined,進 GUI 人工佇列 |

### Sanity 檢查(不過就降到下一級)

- 解析結果必須落在 `[earliest_plausible, now]`;`earliest_plausible` 進
  config(預設 1995-01-01),epoch 類來源額外收緊到 ≥2010(WeChat 問世前
  不可能有 mmexport)。
- 「相機出廠預設日」(2000:01:01、1980:01:01、1970 epoch 0 附近)視為
  可疑:不自動採信也不自動覆寫,進人工佇列。
- 精度規則:只有日期沒時間 → 補 `12:00:00`;只有年月 → 補 15 日;
  只有年 → 補 07-01,且**必須**帶 precision 標記。

### 時區

epoch 類來源(第 3 級)是絕對時間,轉在地時間需要時區:用 config 的
`default_timezone`(預設 Asia/Taipei),整批覆寫可由 GUI per-batch 調整
(出國期間收的檔)。寫入不含 offset 欄位就是在地牆鐘時間,與相機慣例一致。

## 理由

1. Timeline 可用性 > 精確性(Metal 拍板),但**可逆性是底線**:
   keyword 讓所有估計值在 Immich 內可搜尋(搜 `phoxif:date-estimated`
   即得全部待覆核清單),catalog 讓每一筆可還原。
2. 階梯把「該用哪個線索」從自由心證變成查表,弱模型可執行、可測試
  (每一級都能寫成 pytest case)。

## 被否決的替代方案

- **寧缺勿錯**:照片沉到匯入日,timeline 廢掉,違背專案目的。
- **全部進 GUI 人工逐張確認**:量級(數萬張)不可行;人工只留給落空與可疑者。
- **只記在 catalog、不寫檔案**:換一套工具就丟;檔案本身是唯一跟著資產走的載體。
- **用 XMP sidecar 檔而非寫入原檔**:sidecar 會與檔案分離(rsync 漏帶、
  改名斷鏈);且我們寫入前有 catalog 原值備份,可逆性已保障。

## 重估訊號

- 實測發現 Immich 不索引 IPTC/XMP keywords(版本行為變化)→ 改標記載體
  (description 欄位或 sidecar),階梯與極性不變。
- 人工佇列長期 >20% → 階梯覆蓋率不足,回頭補新 heuristic(先寫 ADR 增補)。

## 正例/反例

- ✅ 正例:`mmexport1705312245678.jpg` → epoch 1705312245678ms =
  2024-01-15 17:50(Asia/Taipei)→ 在合理區間 → 寫 DateTimeOriginal
  `2024:01:15 17:50:45` + keywords `phoxif:date-estimated`,
  `phoxif:date-src:filename-epoch`;catalog 記原值(空)。
- ❌ 反例:mmexport 檔直接把檔案 mtime 寫進 EXIF(跳過階梯查表);
  或寫入了估計日期但沒打 keyword(不可逆污染,違反本 ADR 底線)。
