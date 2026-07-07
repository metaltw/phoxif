# ADR-0002:SQLite catalog 作為跨機器管線脊椎

狀態:Accepted(2026-07-07)

## 背景

跨電腦收集與去重是 Metal 排序第一的痛點。現有狀態載體只有:
單次 scan 的記憶體、單資料夾的 `.phoxif_log.json`(undo 用)。
「這張照片在別台電腦出現過嗎」「上次處理到哪」「這個日期是誰寫的、
原值是什麼」——都需要跨批次的持久狀態。

## 決策

- 單一 SQLite 檔為整條管線的唯一事實來源(single source of truth)。
  路徑由 `config.yaml` 指定(gitignored;例如 `catalog_db: ~/phoxif/catalog.db`),
  程式碼不得硬編碼。
- 以 **sha256(檔案全內容)為主鍵**。同 hash = 同檔案,無論它出現在幾台機器。
- 核心欄位(首版 schema,實作時可加不可刪):
  - 身份:`sha256`(PK)、`phash`(圖片才有)、`size`、`ext`、`media_type`
  - 證據(Ingest 當下寫入,之後唯讀):`source_machine`、`original_path`、
    `original_name`、`original_mtime`、`original_btime`、`ingested_at`、`batch_id`
  - 補值溯源:`date_written`、`date_source`(heuristic 名)、`date_confidence`、
    `date_original_value`(寫入前的 EXIF 原值,undo 用)、
    `gps_written`、`gps_source`、`gps_original_value`
  - 狀態機:`status`(ingested → deduped → enriched → reviewed → archived
    | duplicate | quarantined)、`dup_group_id`、`kept_sha256`(輸家指向贏家)
  - 歸檔:`archived_path`(NAS 樹內相對路徑)、`archived_at`
- 同一檔案在多處出現 → `sightings` 附表(sha256, machine, path, mtime, seen_at),
  主表一列、目擊多列。
- Schema 變更紀律:任何欄位增刪都要 migration script(哪怕只是
  `ALTER TABLE ADD COLUMN`)+ 版本號(`PRAGMA user_version`),禁止
  「刪掉 db 重掃就好」——catalog 裡的 ingest 證據重建不出來。

## 理由

1. sha256 主鍵讓「跨機器精確去重」變成一句 SQL。
2. 溯源欄位讓估計值可審計、可回復(ADR-0004 的基礎)。
3. SQLite:零部署、單檔可備份、Python stdlib 內建、單機工作流(MBP 是
   處理中樞)完全夠用。
4. 證據欄位唯讀 = 資料救援的「保全現場」落到 schema 層面。

## 被否決的替代方案

- **每資料夾 JSON(現 `.phoxif_log.json` 模式擴充)**:無法跨批次查詢,
  去重要 O(n²) 掃檔案。log 檔保留原用途(單 session undo),不升格。
- **直接用 Immich 的 DB / API 當事實來源**:綁死下游工具;且 Enrich 發生
  在進 Immich 之前,時序不通。
- **PostgreSQL / 其他 server DB**:單機工具,殺雞用牛刀,部署成本無回報。

## 重估訊號

- 照片量 >50 萬且查詢變慢:先加索引與 phash 分桶,仍不夠才考慮換儲存。
- 出現「多台機器同時寫 catalog」的需求(目前設計是 MBP 單點處理):
  需要重新評估並鎖定並發模型,回報使用者。

## 正例/反例

- ✅ 正例:實作 Ingest 時先寫 migration `0001_init.sql` 建表 + user_version=1,
  測試用 tmp_path 建全新 db 跑一輪 ingest→查詢。
- ❌ 反例:「開發期 schema 還會變,先不做 migration,壞了重建」——
  重建會抹掉已記錄的來源機器/原始 mtime 證據,那正是不可再生的部分。
