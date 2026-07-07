# Architecture Decision Records

架構決策記錄。**Accepted 的 ADR 是約束,不是建議**——後續 AI session 不得
重新評估或提替代方案;唯一例外:決策與可觀察事實衝突(API 消失、依賴棄用、
測不過),此時停下回報使用者,不得默默繞過。

| # | 決策 | 狀態 |
|---|---|---|
| [0001](0001-pipeline-stages.md) | 五階段管線,去重先於補值,歸檔後唯讀 | Accepted |
| [0002](0002-catalog-sqlite.md) | SQLite catalog 作為跨機器管線脊椎 | Accepted |
| [0003](0003-immich-external-library.md) | Immich 走 External Library,不用上傳 API | Accepted |
| [0004](0004-date-backfill-provenance.md) | 日期補齊:盡量填 + 可逆標記 + 信心階梯 | Accepted |
| [0005](0005-gps-backfill-conservative.md) | GPS 補齊:保守極性,寧缺勿錯 | Accepted |
| [0006](0006-exif-write-safety.md) | EXIF 寫入單一 choke point + grep 閘門 | Accepted |
| [0007](0007-legacy-cli-disposition.md) | 四支 legacy CLI 的處置(凍結/修復/吸收) | Accepted |

## 格式

每份 ADR 含:背景 / 決策 / 理由 / 被否決的替代方案 / 重估訊號
(什麼訊號出現時應回頭重新評估此決策——出現了才准重開討論)。

## 何時寫新 ADR

符合任一即寫(對照全域 judgment-rubrics R6 的「架構決策」定義):
新增/更換依賴或框架、改資料格式或 schema(含 catalog)、改對外介面
(API 路由/CLI 參數/檔案輸出格式)、任何「以後很難改回來」的選擇。
純實作細節(命名、內部結構、演算法選擇但介面不變)不寫 ADR。
