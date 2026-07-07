# ADR-0003:Immich 走 External Library,不用上傳 API

狀態:Accepted(2026-07-07;Metal 訪談時未定案,但查證發現其 Immich
生產環境已以 external library 模式運作——私有維運 repo 中已有對應的
trash 清理工具 immich_cleaner。本 ADR 與既有生產環境對齊,非新選擇。)

## 背景

管線終點是「照片在 NAS 上的 Immich 裡統一被整理、可掃描歸隊」。
兩條路:phoxif 透過 Immich API 上傳(Immich 管儲存),或 phoxif 把檔案
排進 NAS 資料夾樹、Immich 以 external library 唯讀掛載並定期 scan。

## 決策

- **External Library 模式**:phoxif 的最終產出物就是 NAS 上一棵穩定的
  資料夾樹;Immich 掛載它、scan 它,不擁有它。
- phoxif **不實作** Immich 上傳 API client。
- 歸檔樹佈局:`YYYY/YYYY-MM/<檔名>`(年/年-月兩層;檔名沿用 phoxif
  rename-by-date 的 `YYYYMMDD_HHMMSS[_n].ext` 慣例,估計日期的檔案同樣
  適用——它有標記,見 ADR-0004)。進樹即唯讀(ADR-0001 鐵律)。
- Enrich 寫入的 EXIF/XMP 會在 Immich scan 時自然被讀取,不需要對
  Immich 做任何 metadata 推送。

## 理由

1. **檔案主權**:相簿是幾十年資產,Immich 只是當前的索引工具。External
   library 下,換工具 = 換個掃描器,檔案樹不動。
2. **phoxif 的產出物本來就是資料夾樹**:API 上傳會讓 Immich 以自己的
   內部佈局收檔,phoxif 的整理成果變成過渡品。
3. **與既有環境一致**:Metal 的 Immich 已掛 external library,配套清理
   工具已存在;API 上傳反而要重建運維流程。
4. 備份/驗證直觀:`rsync -a --checksum` 一條指令能對帳。

## 被否決的替代方案

- **Immich API 上傳**:能即時拿 asset id、能雙向同步,但儲存主權交給
  Immich、綁定其版本行為;對「歸檔」這種一次性寫入場景,雙向同步是
  用不到的能力。
- **兩者混用**(平常 external、補傳走 API):兩套路徑兩套故障模式,
  對單人工作流是純負擔。

## 重估訊號

- 想要 phoxif 讀 Immich 的 ML 結果(人臉/CLIP)輔助整理,或自動建
  album:此時加一個**唯讀** API client(讀 Immich、不寫檔案),儲存
  仍走 external library。這不推翻本 ADR,是擴充。
- Immich 官方棄用或大改 external library 功能:重開評估。

## 正例/反例

- ✅ 正例:Archive 階段 = 「複製到 NAS 樹(保留 mtime)→ 校驗 sha256 →
  catalog 記 archived_path → 提示使用者觸發/等待 Immich scan」。
- ❌ 反例:「順手」在 Archive 之後呼叫 Immich API 改資產日期——
  日期該在 Enrich 階段寫進檔案本身,寫進 Immich DB 的值換一套系統就丟了。
