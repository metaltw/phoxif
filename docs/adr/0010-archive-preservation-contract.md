# ADR-0010:歸檔、Live Photo/AAE、來源邊界與快照安全契約

狀態:Accepted(2026-07-15)

## 背景

Archive 是 phoxif 首次寫入主收藏庫的階段。單檔 JPEG 的成功不足以代表照片
資產完整：Live Photo 包含影像與短片，Apple 編輯可能另有 AAE；截圖／文件
雖不是主要照片，也不能因分類誤判而消失。另一方面，收藏庫若誤設在來源樹或
staging 內，掃描與歸檔會互相吞入，造成重複、遞迴或來源污染。

## 決策

1. `sources.root_path` 保存 ingest 時的 canonical source root。Archive 執行前
   拒絕 archive root 與任一 source root、staging、catalog 的任一方向重疊。
2. archive root 必須已存在且包含內容正確的 `.phoxif-archive-root` sentinel；
   路徑祖先不得是 symlink。
3. `plan()` 是零寫入預覽；批准 fingerprint 綁定 root、batch、source path、
   source root、目前 hash、大小、record kind、group 與目的相對路徑。
4. 發布流程為 temporary copy → SHA-256 讀回校驗 → 不覆寫發布 → 唯讀 →
   catalog/operation transaction。中斷後只接受 hash 相符的既有目的檔續跑。
5. Live Photo 影像/影片與所屬 AAE 是同一 archive group，共用 basename；
   catalog 只在整組驗證完成後一起標記。配對不完整時整組保留、不猜。
6. screenshot／文件歸檔到 `_non_photos/<category>/`，不自動刪除。
7. 每次成功歸檔產 SQLite backup snapshot，容量預檢以 page count × page size
   計入 WAL；只在使用者同一次批准中輪替保留最近 8 份。
8. 來源與 staging 在 archive 後仍保留；清理必須是獨立、明確批准的操作。

## 理由

- 可尋回性高於省空間；錯刪一張不可逆，多留一份只增加容量。
- group commit 避免 Live Photo 在 catalog 看似完成、實際只剩靜態影像。
- source root 是資料集邊界；只檢查單一 working file 無法阻止收藏庫寫回來源樹。
- SQLite 主檔大小不包含尚在 WAL 的已提交頁面，不能作為快照容量上限。

## 被否決的替代方案

- **只靠目的路徑不存在就寫入**：無法防掛載點錯誤、symlink 與來源重疊。
- **Live Photo 成員逐檔 commit**：中斷時會產生半套資產。
- **歸檔成功就自動清 staging／來源**：把可驗證的 copy 動作變成破壞性搬移。
- **非照片直接送垃圾桶**：分類器誤判會違反產品最高優先序。

## 重估訊號

- catalog 改為多 writer 或遠端資料庫：需重設 group transaction 與快照策略。
- 主收藏庫改用 object storage：sentinel、symlink 與 atomic publish 契約需重寫。
- AAE/Live Photo 格式出現可驗證的新配對欄位：可擴充 identity 規則，但仍須整組保全。

## 正例/反例

- ✅ 收藏庫設在來源資料夾的子目錄：execute 在寫入前整批拒絕。
- ✅ Live Photo 影片發布失敗：影像可能已落盤供重試，但 catalog 全組仍未完成。
- ❌ AAE 找不到 owner 就丟棄：應保留來源證據並顯示待處理原因。
