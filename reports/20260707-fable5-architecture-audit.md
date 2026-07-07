# phoxif 架構診斷與行動計畫(Fable 5 開釋)

日期:2026-07-07。依據:全 repo 偵察(HEAD `e179729`,main,clean tree)+ Metal 訪談。
(同名 `.html` 為手寫圖解版,請勿用 md2report.py 覆蓋;本 md 是來源稿。)
本報告是一次性診斷;長期有效的決策已轉寫為 `docs/adr/`,行動項在 `TODO.md`,
驗證方法在 `docs/quality.md`,路線在 `docs/roadmap.md`。

## TL;DR

1. 專案的核心價值鏈(跨電腦收集 → 去重 → 補 metadata → 歸檔 NAS/Immich)**沒有脊椎**:
   缺一個跨批次的 catalog,現有工具都是「單資料夾、單次執行」思維。
2. 安全規則「紙上嚴格、程式碼違規」:design.md 的硬規則被自己的程式碼違反
   (`-overwrite_original` api 4 處/全 repo 8 處、`convert.py` 無條件刪原始影片)。
3. 零測試。任何重構都沒有安全網(本 session 已 bootstrap `tests/`)。

---

## 一、最大的三個架構風險

### 風險 1:單資料夾工具 vs 批次管線的錯位(嚴重度:最高)

**現況**:GUI 是「開一個資料夾 → scan → review → execute」的工具型 app;
狀態只活在單次 scan 的記憶體與該資料夾的 `.phoxif_log.json`。
但使用者的實際問題是**跨多台電腦、多個資料夾**的收集與去重——
這需要跨批次、跨機器的持久狀態。

**判準(什麼時候你會撞牆)**:當你要回答「這張照片在別台電腦出現過嗎?」
「這批檔案上次處理到哪?」「這個估計日期是哪個 heuristic 寫的、原值是什麼?」
——現有架構全部答不出來。

**修法**:建 SQLite catalog 作為管線脊椎(見 ADR-0002),
管線分五階段且順序固定(見 ADR-0001)。GUI 保留,改為駕駛艙,不再是狀態容器。

### 風險 2:安全規則與程式碼互相矛盾,違規會繁殖(嚴重度:高)

**現況**(全部經 code 驗證):
- design.md:19-23 明令 `exiftool -overwrite_original` NEVER used;
  但 `phoxif/api/actions.py:190,261,308,329` 有 4 處直接使用
  (legacy CLI 另有 4 處,全 repo 基線共 8,分層處置見 quality.md G1)。
- design.md 明令影片原檔 NEVER auto-deleted;但 `phoxif/convert.py:167`
  轉檔後**無條件 `src.unlink()` 永久刪除原始影片**,無回收桶、無驗證閘。
- `phoxif/sorter.py:401` 的網頁 Del 按鈕是永久刪除,無 trash、無 log。
- `phoxif/api/actions.py:300-303`(`_rotate_pillow`)原地覆寫原檔,
  無 temp+atomic replace,寫到一半失敗會毀檔。

**為什麼這比單純的 bug 危險**:AI 開發者遇到「文件說 A、程式碼做 B」時,
多數會模仿既有程式碼(B),於是違規模式繁殖。文件的硬規則形同虛設。

**判準(機器可查)**:
`grep -rn 'overwrite_original' phoxif/` 非零、
`grep -rn '\.unlink()' phoxif/` 命中使用者檔案路徑 = 規則仍在被違反。

**修法**:所有 EXIF 寫入收斂到單一 choke point 模組(見 ADR-0006),
搭配 grep 閘門(`docs/quality.md`);P0 修復清單見 `TODO.md`。
design.md 的規則措辭需同步精緻化(「禁止對使用者原檔 in-place 寫入」,
而非一刀切禁 flag)——屬「先說再改」層級,已列 TODO 待 Metal 核准。

### 風險 3:零測試 + 外部工具行為假設散落各處(嚴重度:高)

**現況**:全 repo 無任何測試(pytest 連依賴都不是);對 exiftool/ffmpeg 的
行為假設(輸出格式、tag 名、錯誤模式)散落在 5+ 個模組,無錯誤處理的
`subprocess.run` 在 legacy 腳本裡裸奔(write_gps.py:37、organize.py:113、convert.py:30)。

**判準**:改 `scanner.py` 或 `classifier.py` 的任何一行,沒有機器訊號告訴你
有沒有弄壞別的東西。

**修法**:本 session 已建 `tests/` 骨架(conftest fixture factory + seed tests,
見 `docs/quality.md`)。之後的紀律:改 heuristic 先加 case(先紅後綠)。

---

## 二、最容易讓 AI 開發者走偏的四個地方

| # | 偏航 | 為什麼會偏 | 正確方向(判準) |
|---|---|---|---|
| 1 | **先做日期補齊、後做去重** | 「WeChat/LINE 補日期」是明星功能,直覺先做 | 近重複組是 metadata 恢復通道:WeChat 轉存檔常是「別處存在的原檔」的壓縮版,先去重就不用估日期。順序鐵律見 ADR-0001 |
| 2 | **把估計日期直接寫入、不留標記** | 「反正 Metal 說盡量填」 | 估計值必須可逆、可審計:XMP keyword 標記 + catalog 記原值,見 ADR-0004。不標記 = 不可逆污染 |
| 3 | **信任跨機器複製後的 mtime** | mtime 看起來就是個日期 | 檔案一經複製/下載,mtime 證據可能已毀。只有 ingest 當下從來源機器記錄的 mtime 才可用(且是最低信心來源),見 ADR-0004 |
| 4 | **對轉存檔做時間鄰近 GPS 推斷** | 「這張前後的照片都在東京,它應該也是」 | 轉存檔的日期本身是估計的(收到時間≠拍攝時間),疊加推斷=誤差相乘。GPS 極性是保守:錯的 GPS 比沒有更糟,見 ADR-0005 |

## 三、最值得先還的三筆技術債(P0,詳見 TODO.md)

1. **`convert.py:167` 無條件刪原始影片** — 全 repo 最危險的一行。
   改 send2trash + 轉檔驗證閘(時長/串流數比對通過才進垃圾桶)。
2. **EXIF 寫入安全收斂**(actions.py 4 處 + `_rotate_pillow` 原地覆寫)
   — 建 `exif_writer` choke point,一次修完。
3. **依賴誠實化** — `onnxruntime`/`huggingface_hub` 被 import 卻未宣告
   (orientation_ai.py:52-53),靠 broad except 靜默降級,會遮真 bug;
   dead 的 `ai` optional-dependency group;`/opt/homebrew/bin/jpegtran` 硬編碼。

## 四、既有資產盤點(別重造的輪子)

- **Immich 已在生產環境以 External Library 模式運作**:私有維運 repo
  (war_room)已有對應的 external library trash 清理工具(immich_cleaner)。
  ADR-0003 據此定案 external library 路線,phoxif 不做 API 上傳。
- 分類器已有 WeChat/LINE/WhatsApp/Telegram 檔名 pattern(classifier.py:76-94),
  日期補齊的 heuristic 可直接沿用這些 regex,不要另寫一套。
- `similar.py` 已有 perceptual hash 分組——近重複去重的核心演算法已存在,
  缺的是跨批次的持久化與「哪份贏」的決策規則(ADR 已定)。

## 五、行動計畫摘要(正本 `docs/roadmap.md`)

| Phase | 內容 | 規模 | 自主性 |
|---|---|---|---|
| P0 | 安全止血(convert.py 刪片閘、sorter trash、exif_writer、actions 收斂) | S | 自主 |
| P1 | SQLite catalog + Ingest 收集器(保全證據、冪等) | M | schema 需審核 |
| P2 | 跨機器去重(sha256 → phash,「哪份贏」規則) | M | 首批 dry-run 抽查 |
| P3 | Enrich:日期信心階梯 → GPS 保守補齊 | M | 自主+首批抽查 |
| P4 | Archive NAS 樹 + Immich external library scan | S-M | NAS 寫入必問 |
| P5 | GUI 駕駛艙化(管線裝進五步流程) | M-L | UX 需審核 |

安全基線收斂:`-overwrite_original` 8(現在)→ 4(P0-4 後,剩 legacy)→ 0
(Phase 3 吸收後),G1b 閘門每輪盤點只准變少。

## 六、本次 session 交付物索引

| 檔案 | 內容 |
|---|---|
| `docs/adr/0001..0007` | 七項架構決策(管線順序、catalog、Immich、日期/GPS 極性、EXIF 安全、legacy 處置) |
| `docs/roadmap.md` | 分階段路線圖(對齊 Metal 優先序:收集去重 → 日期補齊) |
| `docs/quality.md` | 機器驗證訊號、工作迴圈落地、DoD、grep 閘門 |
| `TODO.md` | 弱模型待辦清單(含驗收條件與建議模型) |
| `tests/` | pytest 骨架 + seed tests |
| `CLAUDE.md` | 重寫為 ≤150 行路由 |
| `.claude/rules/fable5-handoff.md` | 給未來 session 的交接信 |
