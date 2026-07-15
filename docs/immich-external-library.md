# Immich External Library 交接

phoxif 的終點是一棵由使用者持有、歸檔後不再改名或改寫的照片樹。Immich
只負責索引；phoxif 不呼叫 Immich 上傳 API。

## 一次設定

1. 將 `config.yaml` 的 `archive_root` 指到主收藏庫實體路徑，並在**已確認掛載
   正常的 NAS 根目錄**建立 `.phoxif-archive-root`，內容只有一行
   `phoxif-archive-root-v1`。phoxif 每次 plan／execute 都會驗證這個 sentinel；
   NAS 掉線只剩空 mountpoint 時會拒絕寫入。
2. 在 Immich 的 `immich-server` container 掛載同一棵樹，建議使用唯讀
   volume，例如 `<host-library-path>:/mnt/phoxif-library:ro`。
3. 以 Immich 管理員身分進入 `Administration → External Libraries`，建立或
   編輯 library，加入 **container 看到的路徑** `/mnt/phoxif-library`。
4. 執行 `Scan`。可在 `Administration → Jobs` 確認 Library、Generate
   Thumbnails、Extract Metadata 工作正在執行。

Immich 官方文件特別提醒：import path 必須是 container 內的路徑，不是 NAS
host 路徑；network drive 的自動 watching 可能無法工作，這時應使用定期 scan。
參考：[External Libraries](https://docs.immich.app/features/libraries/)、
[External Library Guide](https://docs.immich.app/guides/external-library/)。

## 每批歸檔後

1. phoxif 顯示「已安全歸檔」，並確認失敗數為 0、catalog 快照成功。
   每日 snapshot 會覆新同日版本，並在使用者核准的歸檔動作中只保留最近 8 份。
2. 在 Immich 對 external library 執行 `Scan`，或等待排定的 scan interval。
3. 首次正式批次抽查 10 筆：
   - timeline 日期與 phoxif 預覽一致；
   - `phoxif:date-estimated` 可搜尋到估計日期檔；
   - 有 GPS 的照片能出現在地圖，保持無 GPS 的照片不應被誤定位；
   - 影片可以播放；若含 Live Photo，照片與短片仍為同一資產。
   - 若有 AAE，檔案應與 owner 共用 basename；`_non_photos/` 不應混入主要照片樹。

## 不可做

- 不要讓 Immich 以可寫方式掛載主收藏庫；否則 Immich UI 可刪除檔案或建立
  sidecar，破壞 phoxif 的「歸檔後唯讀」前提。
- 不要在歸檔後手動移動或改名檔案。Immich 會把新路徑視為新資產，既有 album
  或 metadata 關聯可能遺失。
- 不要把 `_phoxif/` 加為另一個 import path；該目錄只保存 catalog 快照。

## 對帳與故障判讀

- phoxif 成功、Immich 看不到：先確認 container 內可讀到 import path，再看
  Jobs；這通常是 mount／scan 問題，不代表歸檔失敗。
- phoxif 顯示部分失敗：不要先 scan，也不要手動補 copy；修正錯誤後在 phoxif
  安全重試，既有相同 hash 的目的檔會由 operation ledger 恢復。
- `date-quarantined` 項目不會進主收藏庫；先完成日期人工確認，再重新規劃。
