# phoxif Roadmap(2026-07-07 定版)

排序依 Metal 訪談:①跨電腦收集與去重 ②WeChat/LINE 日期/GPS 補齊。
管線順序與各項決策見 `docs/adr/`(Accepted ADR 是約束)。
每階段交付「後端模組 + 測試」先行,GUI 接線隨後;驗收指令見 `docs/quality.md`。

規模:S ≈ 單 session 可完;M ≈ 2-3 sessions;L ≈ 需拆子計畫。
自主性:「自主」= Sonnet 級可自行完成並收工;「審核」= 完成後需 Metal 過目才算關閉。

---

## Phase 0:安全止血 + 驗證地基(規模 S,自主,詳項見 TODO.md P0)

**目標**:把「會毀資料」的既有程式碼修掉,建立 exif_writer choke point。
測試骨架已於 2026-07-07 建成(tests/,22 cases)。

**驗收**:
- `convert.py` 不再 `unlink()` 原始影片:轉檔驗證閘 + send2trash,有測試。
- `exif_writer.py` 存在且有 temp+read-back+atomic replace 測試(ADR-0006)。
- actions.py 4 處 `-overwrite_original` 與 `_rotate_pillow` 改走 exif_writer。
- grep 閘門(quality.md)全過;`uv run pytest -q` 全綠。

**最容易做錯的一步**:改 `_rotate_pillow` 時忘了 rotate 後 EXIF Orientation
要重設(現行 actions.py:308 有做)——重構時保留該行為並寫測試鎖住。

## Phase 1:Catalog + Ingest(規模 M,schema 需審核,其餘自主)

**目標**:`phoxif/catalog.py`(SQLite,ADR-0002 schema + migration)+
Ingest 收集器:掃來源根目錄 → sha256/phash → 記錄證據(來源機器、原始
路徑、mtime)→ 入庫。CLI 先行(`python -m phoxif.ingest --source <label> <dir>`)。

**驗收**:
- 全新 db 上 ingest 兩個模擬來源資料夾(tmp fixture),sightings 正確合併同 hash。
- 重跑同來源 = 冪等(不重複入庫),有測試。
- mtime 在複製進工作區後與 catalog 記錄一致(證據保全),有測試。
- schema migration 機制有測試(user_version 升級路徑)。

**最容易做錯的一步**:把 ingest 寫成「移動檔案」——Phase 1 的 ingest
**只讀不動**(登記 + 可選複製到工作區),原始檔留在原地,搬移是
Archive 階段的事。動了就毀證據。

## Phase 2:跨機器 Dedupe(規模 M,「哪份贏」規則首批結果需審核)

**目標**:catalog 內 exact dup(同 sha256 多 sightings)與 near-dup
(phash 距離 ≤ 門檻,沿用 similar.py 演算法)分組;
「哪份贏」規則:EXIF 完整度 > 解析度/檔案大小 > 相機命名(IMG_/DSC)
勝過轉存命名(mmexport/LINE);輸家標 `duplicate` + `kept_sha256`。
GUI:dup group review 畫面沿用既有 DuplicateGroup/SimilarDetail 元件擴充。

**驗收**:
- 規則單元測試:原檔 vs WeChat 壓縮版 → 原檔贏,理由欄位可讀。
- 首批真實資料跑 dry-run 報告(贏家/輸家清單)→ Metal 抽查後才准 execute。
- 執行動作只有 trash(send2trash)+ catalog 標記,無永久刪除。

**最容易做錯的一步**:把 near-dup 門檻調鬆去「多抓一點」——誤殺的是
不可再生的原檔。門檻進 config,預設保守,調整需附誤判抽查數據。

## Phase 3:Enrich — 日期補齊,然後 GPS(規模 M,自主;首批結果抽查)

**目標**:實作 ADR-0004 信心階梯(heuristic 查表 + sanity 檢查 + keyword
標記 + catalog 溯源),寫入走 exif_writer;之後 ADR-0005 GPS 兩來源。
GUI:date review 佇列(落空/可疑者人工處理)。

**驗收**:
- 階梯每一級至少一個 pytest case(mmexport epoch、WhatsApp 檔名、
  資料夾名、精度標記、sanity 拒絕)。
- 寫入後 exiftool read-back:DateTimeOriginal 與 keywords 都在,有整合測試。
- 抽 20 張真實轉存檔人工核對估計值合理性(Metal 或 GUI 抽查)。
- 在 Immich 測試例(一張標記照片)確認 keyword 可搜尋(交接信列此為待驗證)。

**最容易做錯的一步**:時區。epoch 是 UTC 絕對時間,直接 `fromtimestamp()`
用系統時區在旅行照片上會錯 1-16 小時;一律走 config `default_timezone`
且測試要固定 tz 斷言(不依賴執行機器的系統時區)。

## Phase 4:Archive + Immich 對接(規模 S-M,NAS 寫入需審核)

**目標**:歸檔器:catalog 選 `enriched` → 複製到 NAS 樹 `YYYY/YYYY-MM/`
(ADR-0003)→ sha256 校驗 → 標 `archived`。進樹唯讀。
文件化 Immich external library 掛載與 scan 流程(不寫 Immich API code)。

**驗收**:
- tmp 目標樹的歸檔測試:佈局正確、校驗通過、catalog 更新、重跑冪等。
- 真實 NAS 首跑:dry-run 清單 → Metal 核准 → 執行(NAS 寫入屬「動前必問」)。
- Immich scan 後抽 10 張:timeline 日期正確、估計標記可搜尋。

**最容易做錯的一步**:對「已在 NAS 樹裡」的檔案重複歸檔成 `_1` 副本
——歸檔前必查 catalog `archived_path`,冪等是驗收條件不是加分項。

## Phase 5:GUI 駕駛艙化(規模 M-L,UX 變更需審核)

**目標**:把管線五階段裝進既有五步 GUI(Scan→Review→Confirm→Execute→Done
映射到 Ingest→Dedupe/Enrich review→Confirm→Execute→Archive 報告);
批次/來源管理畫面;人工佇列(日期落空、GPS 建議確認)。

**驗收**:走完一條「兩來源 → 去重 → 補值 → 歸檔」的端到端 demo
(可用 /verify 類流程實跑),操作全程無 CLI。

**最容易做錯的一步**:在 GUI 層重新實作管線邏輯(routes.py 已有 1039 行
先例)——GUI 只准呼叫管線模組,routes 裡不寫業務規則。

---

## 不做清單(明確出局,別浪費算力)

- Immich API 上傳 client(ADR-0003)。
- 資料夾名自動 geocode 直接寫入(ADR-0005 禁令)。
- 相簿瀏覽器/長駐服務化(memory:tool-type app,open→process→close)。
- 多人/多機並發寫 catalog(單點處理模型,ADR-0002)。
