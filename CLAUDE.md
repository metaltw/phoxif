# phoxif — Photo/Video Rescue & Metadata Toolkit

## What This Is

拯救散落多台電腦的雜亂照片/影片(heic/jpg/png/mov/mp4):收集 → 去重 →
補齊 metadata(尤其 WeChat/LINE 轉存檔遺失的日期/GPS)→ 歸檔 NAS →
Immich(external library)索引。GUI-first 工具型 app(open→process→close),
不是 CLI 工具、不是相簿瀏覽器。

## CRITICAL: This is a PUBLIC repo

- NO personal paths, GPS coordinates, location names, usernames, hostnames
- All personal config goes in `config.yaml` (gitignored); only
  `config.example.yaml` is committed. New config keys MUST land in both.
- 機器檢查:`docs/quality.md` §1 個資閘門,收工必跑

## 路由表(先讀對應檔再動手)

| 情況 | 讀這個 |
|---|---|
| 任何架構/管線/heuristic 相關工作 | `docs/adr/README.md`(Accepted ADR 是約束,不重新評估) |
| 實作管線模組(schema/介面/閥值/政策矩陣) | `docs/pipeline-design.md`(詳細設計正本) |
| 要做新功能、排優先序 | `docs/roadmap.md` |
| 驗收、測試指令、DoD、紅線 | `docs/quality.md` |
| 修 bug / 衛生工作 | `TODO.md`(含驗收條件與派工建議) |
| 本專案的坑與退化預警 | `.claude/rules/fable5-handoff.md` |
| 一次性報告產出 | `reports/`(沿用既有索引與自包含 HTML 格式) |
| 設計背景(較舊,與 ADR 矛盾時以 ADR 為準並回報) | `docs/design.md`、`docs/workflow.md` |

## 紅線(一票 FAIL,詳見 docs/quality.md §4)

1. 使用者檔案永久刪除——一律 send2trash
2. 繞過 exif_writer 對使用者檔案 in-place 寫入(ADR-0006)
3. 估計 metadata 寫入不帶 `phoxif:*` 溯源標記(ADR-0004/0005)
4. 動 DEPRECATED 凍結模組的功能(ADR-0007)
5. 個資進可 commit 檔案

## Tech Stack & Style

- Python 3.12(uv)、FastAPI + React 19/TS(Vite)、SQLite catalog(規劃中)
- exiftool(EXIF 讀寫)、ffmpeg+VideoToolbox(HEVC)、ONNX(方向偵測)、
  Nominatim(reverse geocoding)
- Type hints、Google docstring;`uv run ruff check .`;前端 strict TS
- 測試:`uv run pytest -q`(fixture 動態生成於 tmp_path,不 commit 二進位)

## 怎麼跑

- App:`python main.py`(pywebview 視窗)/ `python main.py --dev`(瀏覽器+reload)
- 前端建置:`cd frontend && npm run build`
- Legacy CLI(凍結中,見 ADR-0007):`python -m phoxif.<module> --config config.yaml`

## Current State(2026-07-18)

- **已完成並 commit**:Phase 0 安全止血、Catalog+Census+Ingest、catalog-backed
  dedupe/trash、日期與 GPS 補值、preservation-first archive；目前 157 tests。
- **GUI 阻斷修復**:`8212564 fix(gui): restore visible photo workflow`。掃描結果現在
  固定顯示真實縮圖；貼入來源路徑後顯示「已加入／等待掃描」；結果首屏明示
  下一步；縮圖每批 24 個，失敗有可理解 fallback。
- **真實資料 operator walkthrough(2026-07-18 已完成)**:以 config.yaml `base_dir`
  指定的真實資料夾（1,117 媒體檔、36.6GB、999 部影片）跑唯讀掃描 walkthrough，
  playwright 驅動 20 項檢核全過：真實縮圖（影片畫格、HEIC 轉檔皆載入）、來源狀態
  （已加入／等待掃描／掃描中／完成／失敗）、每頁單一主 CTA、損壞檔 fallback、
  24 檔分批載入；來源資料夾 stat 快照前後 diff 為空（零修改）。掃描器略過
  隱藏目錄（.dtrash/.thumbnails/.previews 等衍生物）為預期行為。
- **2026-07-18 red-team(三路攻擊、隔離環境、含全管線真實小子集實跑)已完成並修復**:
  P0×3 全修——re-ingest 會清掉 enrichment 過的工作副本並使 catalog 永久失聯
  (ingest 改為同時信任 `files.current_sha256`)、無權限資料夾被當成空資料夾
  (census 拒絕並報錯)、父子來源重疊自我判重(census 拒絕)。另修:全部 API
  handler async→def + 寫入互斥鎖(長複製期間 UI 不再凍結,附併發實證與回歸測試)、
  錯誤訊息繁中化(連線失敗/HTTP/archive marker)、輸入正規化(file://、引號、
  跳脫空白)、雙擊防護、Back/F5 防護、整理紀錄繁中化、staging 隔離事件浮上 UI。
  測試 157→163;fresh verifier PASS 零 MUST-FIX。
- **誠實殘留(見 TODO.md RT 段,不得宣稱已解)**:中斷後無 resume 路徑、
  ingest 無數字進度、掃描進度條裝飾性、子目錄層級權限缺損仍靜默。
- **Fable 5 下一步**:掃描階段 UX 與 red-team 已收斂;全量 ingest(staging 約需
  36.6GB)等使用者拍板 staging 位置(pipeline-design.md §13);RT-1 resume 路徑
  屬架構級,動工前先提案。
- **工作樹注意**:reports 索引與 product guide 有既存未提交變更；另有 `.DS_Store`、
  `AGENTS.md`、`CLAUDE.md.bak` 等未追蹤檔。不可混入本次 handoff commit。
