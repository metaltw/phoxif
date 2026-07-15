# ADR-0009:metadata 寫入後維持 ingest 身分與重掃批次成員

狀態:Accepted(2026-07-15)

## 背景

ADR-0002 以整檔 SHA-256 當內容身分，但 ADR-0004/0005 又要求把日期與 GPS
寫回工作檔。metadata 寫入會改變檔案 bytes；若直接改主鍵，原始來源重掃時
會被誤認成新照片，也會斷開 sightings 與 operations 的稽核鏈。

另一個實作衝突是 `sightings` 的唯一鍵 `(sha256, source_id, original_path)`：
同一來源路徑第二次掃描必須沿用既有 sighting，但 batch 仍需記得「這次確實
看過它」。只依 sighting 首次建立時的 `batch_id`，後續 batch 會變成空集合。

## 決策

1. `files.sha256` 是**不可變的 ingest 身分**，永遠保存第一次看到的 bytes。
2. migration 0002 新增 `current_sha256`、`current_size`，記錄安全工作檔目前
   經驗證的 bytes。metadata 寫入只更新這兩欄，不改主鍵或證據外鍵。
3. ingest 查重同時接受 stable/current hash；無論命中哪個，都回傳 stable
   identity，後續 sightings、operations、trash 與 archive 一律用 stable SHA。
4. migration 0003 新增 `batch_items(batch_id, sighting_id)`。每次 ingest 都把
   本次 batch 關聯到該來源證據；既有 sighting 不重複建立，batch membership
   仍會新增。去重與 enrich 以 `batch_items` 取本批內容。
5. `sightings` 的原始路徑、檔名與時間證據維持唯讀；`batch_items` 只表達
   「這次批次看見哪筆既有證據」，不複製 SHA，避免兩處內容身分漂移。

## 理由

- stable identity 讓來源重掃、操作記帳與復原鏈在 metadata 改寫後仍可追溯。
- current hash 讓 trash/archive 在實際動檔前驗證「現在這份 bytes」而不是拿
  ingest 時的舊 hash 誤判內容遭竄改。
- batch-items 關聯把「來源證據」與「本次執行成員」拆開，既保留 sighting
  去重，也讓未來每次收件都能完整重跑 dedupe/enrich。

## 被否決的替代方案

- **metadata 寫入後改 `files.sha256` 主鍵**：會級聯破壞不可變證據與稽核鏈。
- **每次重掃都新增相同 sighting**：同一路徑證據無限膨脹，違反既有唯一鍵。
- **把 sighting 的 `batch_id` 更新成最新批次**：抹掉首次目擊證據，舊批次
  也會反過來變空。
- **只用路徑當身分**：檔案搬家或同內容跨電腦時無法精確重逢。

## 重估訊號

- 未來允許同一內容身分同時維護多個獨立工作副本，且各副本 metadata 版本
  不同：`current_sha256` 需下移到 working-copy 表，不再放在 `files`。
- 出現多進程／多機同時寫 catalog：需重新設計 ingest 與 batch membership
  的交易及鎖定模型。

## 正例/反例

- ✅ 正例：同一舊硬碟掃兩次，`sightings=1`、`batch_items=2`；第二批仍能
  看到照片並進日期規劃。
- ✅ 正例：staging 補日期後 stable SHA 不變、current SHA 更新；原始來源重掃
  仍回到同一 File。
- ❌ 反例：日期寫入後用新 hash 建第二筆 File，讓同一張照片變成兩個身分。
