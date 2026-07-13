# phoxif 管線詳細設計(v2,2026-07-14)

正史文件。上游決策:`docs/adr/0001–0008`(本設計是其實作規格,不重複論證)。
路線:`docs/roadmap.md`。驗收:`docs/quality.md`。
設計輸入(2026-07-14 Metal 訪談):總量 < 5 萬檔;**救援 + 長期進水口雙模式**;
人工只審「低信心 + 破壞性」;實檔普查證據見
`reports/20260714-pipeline-design-evidence.md`。

---

## 1. 系統定位:雙模式,同一條管線

| | 救援模式(rescue) | 進水口模式(inbox) |
|---|---|---|
| 對象 | 歷史存量:散落各機器/資料夾的舊照片 | 增量:新收到的 WeChat/LINE 轉存、新收集 |
| 觸發 | 人工發起一個 campaign(接硬碟/掛共享) | 檔案丟進指定 intake 資料夾,開 app 時待處理 |
| 檔案所有權 | 原始檔留在來源,staging 是複本 | intake 內的檔就是唯一本(處理後歸檔) |
| 結束狀態 | 來源清空由人工確認後另行處理 | intake 清空 = 處理完 |

統一抽象:**一切處理都是「某個 Source 的一個 Batch」**。兩模式共用同一條
五階段管線(ADR-0001)、同一個 catalog、同一套政策矩陣(§10);差別只在
入口與檔案所有權。App 仍是工具型(open → process → close),不做長駐監看:
開 app 時掃 intake 資料夾有無新檔,有就提示開新 batch。

**Catalog 是永久記憶**(ADR-0008):跨 campaign、跨年度不重建。進水口模式的
核心能力「這張以前處理過」就是一句 `SELECT status FROM files WHERE sha256=?`。

## 2. 領域模型與狀態機

```
Source 1 ── n Batch 1 ── n Sighting n ── 1 File(身分 = sha256)
```

- **Source**:具名來源(某台電腦、某個舊硬碟資料夾、"wechat-inbox")。
- **Batch**:一次 ingest 執行,帶 mode(rescue|inbox)與統計。
- **File**:內容身分,sha256 為主鍵。同內容出現在三台機器 = 1 File、3 Sightings。
- **Sighting**:某來源某路徑上的一次目擊(證據:原始路徑/檔名/mtime)。

### File 狀態機(唯一合法轉移)

```
            ┌────────────→ duplicate(終態;輸家,已進垃圾桶/待批准)
ingested ──┤
            └→ unique ──→ enriched ──→ archived(終態;唯讀)
                              │
                              └→ quarantined(補值落空/可疑)──(人工)──→ enriched
```

- 轉移只能由對應階段模組執行(ingest/dedupe/enrich/review/archive),
  GUI 不得直接改 status。
- **重逢規則**(進水口模式的關鍵):新 sighting 的 sha256 已是 `archived`
  → 不建新流程,直接標 duplicate(kept = 既有 archived 檔),源檔進
  待刪批次(§10 破壞性政策)。已是 `duplicate`/處理中 → 只追加 sighting。

## 3. Catalog:SQLite schema v1

檔案位置:`config.yaml: catalog_db`(預設 `~/.phoxif/catalog.db`,gitignored
範例寫進 config.example.yaml)。`PRAGMA user_version` 管 schema 版本;
任何欄位變更走 `migrations/000N_*.sql`(ADR-0002 紀律)。

```sql
-- migration 0001_init.sql
PRAGMA user_version = 1;

CREATE TABLE sources (
  source_id   TEXT PRIMARY KEY,          -- slug,如 "old-laptop-d"
  label       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('rescue','inbox')),
  created_at  TEXT NOT NULL              -- ISO8601,下同
);

CREATE TABLE batches (
  batch_id    TEXT PRIMARY KEY,          -- "20260714-1" 樣式
  source_id   TEXT NOT NULL REFERENCES sources(source_id),
  mode        TEXT NOT NULL CHECK (mode IN ('rescue','inbox')),
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  stats_json  TEXT                        -- census 摘要、各階段計數
);

CREATE TABLE files (
  sha256      TEXT PRIMARY KEY,
  size        INTEGER NOT NULL,
  ext         TEXT NOT NULL,              -- 小寫,含點:".jpg"
  media_type  TEXT NOT NULL CHECK (media_type IN ('image','video')),
  phash       TEXT,                       -- 16 hex chars(64-bit),image only
  width       INTEGER, height INTEGER,
  status      TEXT NOT NULL DEFAULT 'ingested' CHECK (status IN
    ('ingested','unique','enriched','quarantined','archived','duplicate')),
  dup_group_id TEXT,
  kept_sha256 TEXT REFERENCES files(sha256),  -- 輸家指向贏家
  live_partner_sha256 TEXT REFERENCES files(sha256),  -- Live Photo 配對(§6.1)
  -- Enrich 溯源(ADR-0004/0005;寫入前原值必存,undo 依據)
  date_written TEXT, date_source TEXT, date_confidence INTEGER,
  date_original_value TEXT,
  gps_written  TEXT, gps_source  TEXT, gps_original_value TEXT,
  -- Archive
  archived_path TEXT UNIQUE,              -- 相對 NAS 樹根
  archived_at   TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE sightings (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT NOT NULL REFERENCES files(sha256),
  source_id     TEXT NOT NULL REFERENCES sources(source_id),
  batch_id      TEXT NOT NULL REFERENCES batches(batch_id),
  original_path TEXT NOT NULL,            -- 來源機器上的完整路徑(證據,唯讀)
  original_name TEXT NOT NULL,
  original_mtime TEXT, original_btime TEXT,
  staging_path  TEXT,                     -- 本機工作區複本;歸檔/清理後置 NULL
  seen_at       TEXT NOT NULL,
  UNIQUE (sha256, source_id, original_path)
);

CREATE TABLE operations (                  -- 管線層 undo/audit ledger
  id         INTEGER PRIMARY KEY,
  batch_id   TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  op         TEXT NOT NULL CHECK (op IN
    ('trash','write_date','write_gps','archive_copy','restore')),
  detail_json TEXT NOT NULL,               -- 原值/新值/目的地,足以回復
  executed_at TEXT NOT NULL
);

CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_phash  ON files(phash);
CREATE INDEX idx_sightings_sha ON sightings(sha256);
```

證據欄位(sightings 的 original_*)寫入後唯讀——程式不提供 UPDATE 路徑。
既有 `.phoxif_log.json` 保留給舊 GUI 單資料夾動作;管線一律寫 `operations`。

狀態名與 ADR-0002 草案的對映(以本 DDL 為準):草案的 `deduped` 改名
`unique`(去重後存活者);草案的 `reviewed` 取消——review 是佇列動作
不是檔案狀態(過人工佇列的檔回到 `enriched`),不要把它加回 enum。

### 近重複檢索(< 5 萬檔的實作選擇)

不建 BK-tree、不建 band 表:全量 phash 載入記憶體
(50k × 8 bytes = 400KB),numpy 位元運算算 Hamming 距離,分塊兩兩比對
50k² / 2 ≈ 1.2e9 次 XOR+popcount ≈ 秒級。每個新 batch 只需
「新檔 × (新檔+全庫)」,更小。**超過 20 萬檔才需要換索引結構**(ADR-0002
重估訊號),介面上把 `find_near(phash, max_dist) -> list[sha256]` 抽成
catalog 方法,屆時只換內部實作。

## 4. 模組結構

```
phoxif/
  core/
    exif_writer.py     # ADR-0006 唯一寫入 choke point(TODO P0-3)
    dates.py           # 檔名/EXIF 日期解析(收斂 P1-7 的兩套邏輯)
  pipeline/
    catalog.py         # schema/migration/查詢;唯一碰 SQLite 的模組
    census.py          # §5 來源普查(只讀)
    ingest.py
    dedupe.py
    enrich_dates.py
    enrich_gps.py
    archive.py
  api/                 # FastAPI:薄路由,只呼叫 pipeline/*,不含業務規則
  (legacy CLI 依 ADR-0007 凍結/吸收)
```

每個 pipeline 模組同時是 CLI(`python -m phoxif.pipeline.ingest --source X`),
GUI 只是另一個呼叫者。介面簽名(實作時不得擅改語意):

```python
census.scan(root: Path) -> CensusReport                    # 零寫入
ingest.run(source_id: str, root: Path, mode: Mode) -> BatchResult
dedupe.run(batch_id: str) -> DedupeResult                  # 只標記,不刪檔
dedupe.pending_trash(batch_id) -> list[TrashItem]          # 給批准畫面
dedupe.execute_trash(batch_id, approved: bool) -> TrashResult
enrich_dates.run(batch_id) -> EnrichResult                 # 寫檔一律經 exif_writer
enrich_gps.run(batch_id) -> EnrichResult
archive.plan(batch_id) -> ArchivePlan                      # dry-run 清單
archive.execute(plan) -> ArchiveResult                     # NAS 寫入,動前必問
```

## 5. Ingest(含「來源普查」前置步)

**來源普查(census)是每個新 Source 的強制第一步**,零寫入,產出:
副檔名/檔名 pattern 分布、EXIF 覆蓋率、mmexport 等 pattern 的 epoch 解析
成功率、預估重複率。用途:
1. 驗證檔名格式假設(mmexport/LINE 的實際格式**尚未在真實檔案上驗證過**
   ——交接信信心表;普查就是每來源的在地驗證,格式不符 = 解析器不啟用
   並回報,而不是寫錯日期)。
2. 給 Metal 一頁摘要:這個來源有什麼、預估要處理多久。

Ingest 本體(每檔):
1. sha256(串流計算)+ 基本 stat → upsert `files` + 插入 `sighting`
   (原始路徑/mtime/btime 證據)。
2. 影像算 phash(HEIC 經 `sips` 轉暫存 jpg 再算;失敗記 NULL 並計數)。
3. rescue 模式:複製到 staging(`cp -p` 保 mtime;先檢查
   staging 磁碟餘裕 ≥ batch 大小 × 1.2,不足則要求縮小 batch)。
   inbox 模式:intake 資料夾就是 staging,不複製。
4. 冪等:`(sha256, source_id, original_path)` UNIQUE——重跑同來源零副作用;
   已 archived 的 sha256 走重逢規則(§2)。

**Ingest 不動來源檔案。**搬移/刪除只發生在:staging 複本(歸檔後清理)、
批准後的 trash(§10)。

## 6. Dedupe

兩層,先 exact 後 near,只處理本 batch 新進的 `ingested` 檔:

1. **Exact**:同 sha256 多 sightings → 自動合併(本來就是同一個 File,
   無破壞性)。「同內容不同檔案」不存在——身分即內容。
2. **Near**:phash 距離 d(64-bit Hamming),但**先過連拍判別**:
   - **連拍判別(2026-07-14 實證新增)**:普查顯示 d≤4 的配對絕大多數是
     連拍/同景連按(112 對集中在 `_N` 尾碼叢集),不是重複。
     配對若「同 source、同原始資料夾、雙方都有原生 EXIF 日期、
     時間差 ≤ 10s」→ 判為 **burst,不是 dup**:預設雙留,
     只進非阻塞的 burst 摘要(可選擇性挑最佳,不自動刪任何一張)。
   - 通過連拍判別後才分帶:
     `d ≤ T_auto`(預設 4)**且有不對稱證據**→ 自動判同組。
     不對稱證據公式(任一成立):一方無原生 EXIF 日期而另一方有;
     或 `min(pixels)/max(pixels) ≤ 0.6`;或 `min(bytes)/max(bytes) ≤ 0.6`
     (轉存再壓縮的特徵);
     對稱配對(雙方證據等強)即使 d=0 也進人工佇列,不自動選贏家。
   - `T_auto < d ≤ T_review`(預設 10):進人工佇列
   - `d > T_review`:非重複
   - 閥值進 config;調整必附誤判抽查數據(roadmap P2 紅線)。
   - 影片不做 near(只 exact);normalize 後的 phash 對旋轉不魯棒,
     方向修正(既有 orientation 功能)應在 dedupe 前跑——階段內順序:
     ingest → orientation 建議 → dedupe → enrich。

### 6.1 領域地雷(dedupe/archive 必守,漏掉會毀資料)

1. **Live Photo 配對**:iPhone 的 .HEIC + 同 ContentIdentifier 的 .MOV
   是一體(exiftool `ContentIdentifier`/`MediaGroupUUID`)。規則:
   - ingest 偵測配對,files 記 `live_partner_sha256`(migration 0002 前
     先放 detail;v1 schema 直接加此欄)。
   - 配對永遠同進退:同組去重(以 HEIC 為主體)、同批歸檔到同資料夾
     同 basename、partner .MOV **豁免** HEVC 轉檔與獨立 near-dup。
   - 拆散配對 = Immich 裡 Live Photo 變兩個資產,一票 FAIL。
2. **iOS 編輯變體**:`IMG_E1234.jpg` 是 `IMG_1234.jpg` 的編輯版,
   near-dup 必然高相似——**不是重複,是刻意保留的兩個版本**。
   規則:同夾同號的 `IMG_E` 變體對不自動判 dup,直接雙留
   (.AAE sidecar 一併歸檔,不算媒體檔但跟著走)。
3. **分類前置**:既有 classifier(screenshot/messaging/document)在
   ingest 階段就跑;screenshot/document 類**不進照片歸檔樹**,
   歸到樹外 `_non_photos/`(沿用既有行為);messaging 類是照片本體,
   正常走管線(它們正是要救援的對象)。
4. **Catalog 自身備份**:catalog 是全部溯源證據,單點檔案。每次
   `archive.execute()` 成功後,把 catalog 快照複製到歸檔樹根
   `_phoxif/catalog-YYYYMMDD.db`(保留最近 8 份)。SQLite 開
   `PRAGMA journal_mode=WAL`,單進程單寫者(ADR-0002 併發邊界)。

**贏家規則(確定性、可測試)**:組內按 tuple 降冪排序,首位為贏家:

```python
key = (
  has_native_exif_date,       # 原生 DateTimeOriginal(非 phoxif 寫入)
  has_gps,
  pixels,                     # width*height,None 視為 0
  is_camera_named,            # IMG_/DSC/YYYYMMDD_HHMMSS 命名
  size_bytes,
  -mtime_epoch,               # 全同時取「最早目擊」那份
)
```

輸家:標 `duplicate` + `kept_sha256` + 進待刪批次(§10)。
**輸家的 metadata 撿骨**:若輸家有贏家缺的欄位(罕見:轉存檔反而有
description),記進 operations detail,不自動寫贏家——進低信心佇列。

## 7. Enrich:日期(ADR-0004 的實作規格)

解析器表驅動(`core/dates.py`),一個 pattern 一列,單元測試逐列鎖:

```python
FILENAME_PATTERNS = [
  # (regex, parser, source_tag, confidence, sanity_range)
  (r"^(\d{8})[-_](\d{6})",        parse_ymd_hms,  "filename-date",  2, (1995, now)),
  (r"^IMG_(\d{8})_(\d{6})",       parse_ymd_hms,  "filename-date",  2, (1995, now)),
  (r"^mmexport(\d{13})",          parse_epoch_ms, "filename-epoch", 3, (2011, now)),
  (r"^wx_camera_(\d{13})",        parse_epoch_ms, "filename-epoch", 3, (2011, now)),
  (r"^IMG-(\d{8})-WA\d+",         parse_ymd,      "filename-date",  3, (2010, now)),
  # LINE 檔名格式:待來源普查實證後才加列(交接信:訓練知識未驗證)
]
```

階梯執行(ADR-0004 表):第一個命中且過 sanity 的來源勝出;寫入一律
`exif_writer.write_tags(path, {DateTimeOriginal, CreateDate},
keywords=[...])`。keywords 依 ADR-0004(`phoxif:date-estimated`、
`phoxif:date-src:<tag>`、精度標記)。

exif_writer 補充規格(ADR-0006 流程之上):temp+`os.replace` 會把檔案
mtime 換成 temp 的——**寫入完成後必須 `os.utime` 還原原 mtime**
(mtime 是證據,也影響 rsync 增量判斷)。

- **媒體型別的第 1 級欄位**(2026-07-14 實證):影像 = DateTimeOriginal
  > CreateDate;影片 = QuickTime:CreateDate(實測 MP4 的 DTO 存在率
  0.1%、CreateDate 100%——影像思維的 DTO-first 會漏掉九成檔案)。
  讀取與寫入都按型別選欄位。
- 時區:epoch 解析用 `config: default_timezone`(預設 Asia/Taipei),
  可在 batch 層覆寫;**測試必須固定 tz 斷言,禁止依賴執行機器時區**。
  注意 QuickTime:CreateDate 規格上是 UTC(exiftool 需配
  `-api QuickTimeUTC=1` 讀寫),與 EXIF 的在地牆鐘時間不同——
  dates.py 統一處理,呼叫端只見在地時間。
- 第 4 級(同批鄰檔內插):同 source 同原始資料夾內,原始 mtime 排序後
  被前後兩檔日期夾擠(前後差 ≤ 48h)→ 取線性內插;夾不住 → 落空。
- 第 5 級(資料夾名日期):對 sighting 的 original_path 各層資料夾段
  (由內而外取第一個命中)套 pattern,**最特定優先**(先試年月日,
  它是年月 pattern 的超集,順序反了年月日永遠不會命中):
  1. `^(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])` → 年月日
  2. `^(19|20)\d{2}[-_.年]?(0[1-9]|1[0-2])?` → 年(+月)
  依命中精度帶 `phoxif:date-precision:*` 標記(ADR-0004 精度規則)。
- 第 6 級(ingest mtime):2026-07-14 實證(本機已管理 corpus,n=1116):
  mtime 與拍攝時間 ≤1min 者 97.5–99.6%——比防禦性假設可靠,**啟用**,
  但仍為最低信心、必標記;且此數字來自「未經多手搬運」的 corpus,
  每個新來源以該來源的普查(census)數字重新評估是否啟用。
- 全落空/可疑(相機預設日等)→ `quarantined`,進人工佇列。

## 8. Enrich:GPS(ADR-0005 的實作規格)

- 來源 1(人工斷言資料夾映射):沿用 config `gps_locations` 資料;
  **既有映射視為已確認**(它們本來就是 Metal 手寫的);新映射由 GUI
  建議(資料夾名 geocode)+ 人工按確認才入庫。
- 來源 2(時間鄰近):僅限 `date_source ∈ {native-exif, filename-date}`
  且同 source 同資料夾、時間差 ≤ 30 min、鄰居本身 GPS 非估計值;
  兩側鄰居距離 > 1km 時落空(移動中不內插)。
- 寫入帶 `phoxif:gps-estimated` 標記;operations 記參照檔。
- **位置資料夾的保存**(新決策點,見 §13):來源資料夾名(如
  `City_A/`)在 ingest 已存於 sighting 證據;若該夾在 gps_locations
  有映射 → 走來源 1 寫 GPS。資料夾名本身可選擇性寫入 keyword
  (`config: folder_name_as_tag`, 預設 off),讓 Immich 可按原資料夾名搜尋。

## 9. Archive

- 目的樹:`YYYY/YYYY-MM/YYYYMMDD_HHMMSS[_n].ext`(ADR-0003;估計日期
  照用——它有標記)。`_n` 只在同秒碰撞時遞增,查 catalog 現有
  archived_path 決定,不看檔案系統(NAS 可能離線)。
- 歸檔路徑**全 ASCII** 是刻意設計:中文檔名跨 macOS(NFD)/NAS(NFC)
  的 unicode 正規化差異會造成 rsync 重傳與 Immich 路徑漂移;
  中文資訊走 EXIF/keyword(UTF-8,在檔案內部,不受路徑正規化影響)。
- 流程:`plan()`(dry-run 清單:src staging → dst 相對路徑)→ Metal 批准
  → `execute()`:複製(保 mtime)→ 讀回 sha256 校驗 → catalog 標
  `archived` + operations 記錄 → 清 staging 複本。
- 冪等:已有 archived_path 的 File 跳過;execute 中斷重跑安全
  (逐檔 commit)。
- Immich:external library 定期 scan 自然吸收;phoxif 不呼叫 API。
- 來源機器上的原始檔:**phoxif 永不遠端刪除**。rescue campaign 收尾時
  產出「本來源已全數歸檔」報告(catalog 對帳),Metal 自行決定清機。

## 10. 自動化政策矩陣(review budget:只審低信心 + 破壞性)

| 決策 | 自動 | 進佇列 | 破壞性批准 |
|---|---|---|---|
| exact dup 合併 | ✔(無破壞性,身分同一) | — | — |
| 連拍判別命中(§6:同源同夾+雙方原生 EXIF+≤10s) | ✔ **雙留,不刪** | 非阻塞 burst 摘要 | — |
| near-dup d≤4 **且有不對稱證據**(§6 公式) | ✔ 判組+選贏家 | — | 輸家丟垃圾桶:**批次一鍵批准** |
| near-dup 對稱配對(任何 d ≤ T_review,含 d=0)或 4<d≤10 | — | ✔ 逐組看圖 | 同上 |
| 日期階梯 1–3 級 | ✔ 寫入+標記 | — | — |
| 日期階梯 4–6 級 | ✔ 寫入+標記 | 摘要清單供翻閱(非阻塞) | — |
| 日期落空/可疑 | — | ✔ quarantine 佇列 | — |
| GPS 來源 1(已確認映射) | ✔ | — | — |
| GPS 來源 2(時間鄰近) | ✔ 寫入+標記 | 摘要清單(非阻塞) | — |
| GPS 新映射建議 | — | ✔ 按資料夾確認 | — |
| 歸檔到 NAS | — | — | **plan 過目後批准**(動前必問) |
| 重逢(inbox 遇已歸檔) | ✔ 標 duplicate | — | 併入待刪批次 |

原則:**寫入型動作(可逆,有標記+原值)自動;刪除型動作(半可逆)批次
批准;NAS 寫入(生產環境)逐 plan 批准**。「摘要清單」= Done 畫面上
可展開的分類統計 + 可點入抽查,不阻塞流程。

## 11. GUI delta(Phase 5 前的最小改動)

- 首頁新增 **Pipeline dashboard**:Sources 卡片(各來源狀態/上次 batch)、
  進行中 batch 進度、四個佇列入口(near-dup 待判、日期 quarantine、
  GPS 待確認、待刪批次)。既有「開資料夾 → 五步流程」保留為
  「快速工具」入口(它就是舊工作流,繼續能用)。
- 佇列畫面複用既有元件:DuplicateGroup/SimilarDetail(near-dup)、
  DateDetail(quarantine)。新增:TrashApproval(待刪批次清單 + 一鍵批准)、
  SourceManager(新增來源/看普查報告)。
- routes 一律薄包裝 pipeline 模組(quality.md 紅線:routes 不寫業務規則)。

## 12. 效能預算(5 萬檔量級,設計驗算)

| 操作 | 估算 | 依據 |
|---|---|---|
| sha256 全量 | ~200GB / ~1GB/s ≈ 4 min | 串流讀,SSD |
| exiftool 批掃 | 50k ÷ ~150/s ≈ 6 min | `-json -r` 單進程;可分夾平行 |
| phash 全量 | 50k × ~30ms ≈ 25 min(HEIC 轉檔另計) | 可平行 ×4 ≈ 7 min |
| near-dup 全庫比對 | 秒級 | §3 numpy 驗算 |
| catalog 任何查詢 | < 10ms | SQLite + 索引,5 萬列 |

結論:單一 rescue batch(數千檔)全程 < 10 min,交互體驗以 batch 為單位
即可,不需要即時串流進度以外的架構(既有 websocket 依賴屆時一併定案,
TODO P2-4)。

## 13. 開放決策點(需 Metal 拍板,不阻塞 P0/P1)

1. **歸檔樹佈局**:現行工作習慣是位置資料夾;ADR-0003 定日期樹。
   建議維持日期樹(穩定、無碰撞、Immich 靠 GPS/標籤呈現地點),
   位置資訊以 GPS(來源 1)+ 可選 keyword 保存。若你強偏好位置樹,
   需修 ADR-0003 並解決同地多次造訪的子夾規則。
2. **staging 位置與容量**:rescue 模式預設 `~/.phoxif/staging/`,
   建議指到外接 SSD(config 可設);<5 萬檔全量約 100–250GB,
   分 batch 處理即可,不需一次全載。
3. **inbox 來源清單**:哪些資料夾當 intake(例:`~/photo_inbox/wechat/`
   一夾一 source)——實作 Phase 1 時定,config 化。

## 14. 與既有制度的關係

- 落實:ADR-0001(階段順序)、0002(catalog,本文件給出 DDL)、
  0004/0005(補值,本文件給出解析表與政策)、0006(一切寫入經
  exif_writer)、0007(legacy 吸收路徑)。
- 新增:ADR-0008(雙模式與自動化政策)——本設計的政策矩陣是其實作。
- Roadmap 對映:§3+§5=Phase 1、§6=Phase 2、§7+§8=Phase 3、§9=Phase 4、
  §11=Phase 5;普查(§5 前置步)併入 Phase 1 交付。
