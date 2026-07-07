# ADR-0007:四支 legacy CLI 的處置(凍結/修復/吸收)

狀態:Accepted(2026-07-07)

## 背景

repo 內有兩代工具並存:legacy 單檔 CLI(`convert.py`、`organize.py`、
`sorter.py`、`write_gps.py`,無 log、無 undo、直接刪改)與新一代
GUI app(`phoxif/api/` + React,send2trash + operation log)。
README 教的是 legacy 用法,`phoxif` console script 卻只開 GUI。
不定案處置,弱模型會繼續在兩邊各自加功能,分裂加深。

## 決策

逐支定性(核心判準:**功能被新架構取代者凍結;不可替代者修安全後保留**):

| 模組 | 定性 | 動作 |
|---|---|---|
| `sorter.py` | **凍結待刪**(功能已被 GUI 的 non-photos/manual sort 取代) | 模組頂部加 deprecation docstring + 執行時 stderr 警告;不再修 bug 不再加功能;其 `unlink()` 刪除按鈕在凍結期間先改 send2trash(一行修復,P0);正式刪除檔案需 Metal 核准 |
| `convert.py` | **修安全後保留**(HEVC 轉檔是管線必要能力,GUI 尚無) | P0:`src.unlink()` → 轉檔驗證閘(時長差 <1s、串流數一致、metadata 完整)通過後 send2trash;之後作為 Enrich/Archive 階段的引擎被吸收 |
| `write_gps.py` | **吸收**(folder→GPS 映射是 ADR-0005 的來源 1) | 心臟(映射+過濾邏輯)移植進管線 + exif_writer;CLI 殼在移植完成後凍結 |
| `organize.py` | **吸收**(reverse-geocode 分類邏輯在 Archive 階段有用) | geocode+快取邏輯移植;`filepath.rename` 直接移檔的行為由管線的 catalog 記錄式移動取代 |

配套:

- README 的 CLI 段落加「legacy,見 docs/adr/0007」注記(P1)。
- 凍結模組的判定是機器可查的:模組 docstring 含 `DEPRECATED` 字樣
  即為凍結;review 時對凍結模組的功能性 diff 一律 FAIL。

## 理由

1. 兩套並存的真正成本不是重複程式碼,是**安全模型分裂**:同一個「刪除」
   在 GUI 走回收桶、在 sorter.py 是永久刪除。使用者(和弱模型)分不清。
2. 「凍結而非立即刪除」:刪檔需 Metal 核准(全域鐵律),且 legacy 腳本
   在管線成熟前仍是唯一可用的批次工具,直接刪會斷 Metal 的現行工作流。

## 被否決的替代方案

- **立即刪除 legacy**:斷現行工作流,且違反刪檔審批。
- **把 legacy 全面翻新成安全版**:等於維護兩套產品;翻新的工能量
  應該投進管線(roadmap),讓管線取代它們。
- **放著不管**:分裂持續加深,新功能會長錯地方。

## 重估訊號

- 管線 Phase 3(Enrich)完成、GPS 寫入走 GUI 後:`write_gps.py` 可提請刪除。
- 管線 Phase 4(Archive)完成後:`organize.py`、`sorter.py` 可提請刪除。

## 正例/反例

- ✅ 正例:需要批次轉 HEVC → 修好驗證閘的 `convert.py` 是正解;
  發現它缺 log → 依本 ADR 它是「修安全後保留」,可以加 operation log。
- ❌ 反例:「sorter.py 的 UI 加個縮圖快取會更好用」——凍結模組,FAIL;
  這個需求應該長在 GUI 的 manual sort。
