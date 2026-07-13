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
| 一次性報告產出 | `reports/`(規範:`~/Documents/git/war_room/standards/repo-reports.md`) |
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

## Current State(2026-07-14)

- **已完成**:GUI 五步流程(scan/dup/similar/rename/orientation/non-photos
  /date-mismatch + undo);ONNX 方向偵測;pytest 骨架(22 tests)
- **已定案**:八份 ADR + **詳細設計正本 `docs/pipeline-design.md`**
  (雙模式、catalog DDL、連拍判別、政策矩陣;實證依據
  reports/20260714-pipeline-design-evidence)
- **下一步**:Phase 0 安全止血(TODO.md P0-1~P0-4)→ Phase 1
  Catalog+Census+Ingest(照 pipeline-design.md §3/§5 實作)
- **已知問題**:`-overwrite_original` 基線 8 處(api 4 + legacy 4,見
  quality.md G1)、main.py 2 個 F401、無 CI——都在 TODO.md
- **待 Metal 拍板**:歸檔樹佈局/staging 位置/intake 清單
  (pipeline-design.md §13,不阻塞 P0/P1)
