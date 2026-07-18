# TODO — 弱模型待辦清單

來源:2026-07-07 Fable 5 架構開釋 session(診斷見
`reports/20260707-fable5-architecture-audit.md`)。功能路線不在此列,
見 `docs/roadmap.md`;此處是修復與衛生類,每項含驗收條件與建議派工。
完成一項就把該行移到檔尾「已完成」段(附 commit hash)。

## P0 — 資料安全(先於一切功能)

| # | 項目 | 驗收條件 | 派工 |
|---|---|---|---|
| P0-1 | `convert.py:167` 轉檔後無條件 `src.unlink()` 刪原始影片 → 改「驗證閘(輸出時長差 <1s、視訊/音訊串流數一致、CreateDate/GPS 已複製)通過後 send2trash」 | 驗證閘有單元測試(偽造壞輸出→不刪);dry-run 不動檔;違規 grep 閘門通過 | sonnet / medium |
| P0-2 | `sorter.py:401` Del 按鈕永久刪除 → send2trash(一行修);同時模組頂加 DEPRECATED docstring + 執行時 stderr 警告(ADR-0007) | grep `unlink` 在 sorter.py = 0;啟動時警告可見 | haiku / low |
| P0-3 | 建 `phoxif/api/exif_writer.py`(ADR-0006:temp→寫入→read-back→atomic replace→log) | 寫入/驗證失敗/undo 三類測試綠;README of module 說明唯一入口地位 | sonnet / high |
| P0-4 | actions.py 4 處 `-overwrite_original`(:190,261,308,329)+ `_rotate_pillow` 原地覆寫(:300-303)改走 exif_writer | G1a 閘門(quality.md 指令區)輸出為空;G1b 全 repo 命中由 8 降至 4(僅剩 legacy,處置見 ADR-0007);orientation 既有行為測試綠(rotate 後 Orientation=1 保留) | 首例 sonnet,其餘 haiku |

依賴順序:P0-3 → P0-4;P0-1、P0-2 獨立可先做。

## P1 — 衛生與誠實化

| # | 項目 | 驗收條件 | 派工 |
|---|---|---|---|
| P1-1 | `onnxruntime`、`huggingface_hub` 用了沒宣告(orientation_ai.py:52-53)→ 進 pyproject dependencies;同時收窄該處 broad `except Exception`(至少分開 ImportError 與執行錯誤並 log) | 乾淨環境 `uv sync` 後 orientation 功能可用;bare/broad except 不吞非預期錯誤 | sonnet / low |
| P1-2 | 移除 dead `[project.optional-dependencies] ai` group(google-genai 已是必要依賴) | `uv sync` 正常;grep `optional-dependencies` 確認移除 | haiku / low |
| P1-3 | `main.py:7,10` 既有 unused import(`sys`、`Path`)清掉,然後 pyproject 加 `[tool.ruff]`(line-length=100,extend-select E722)且全 repo 檢查淨 | `uv run ruff check .` 0 errors,quality.md §1 基線例外註記刪除 | haiku / low |
| P1-4 | `jpegtran` 硬編碼 `/opt/homebrew/bin/`(actions.py:24)→ `shutil.which("jpegtran")`,缺工具時明確報錯訊息 | 單元測試 mock which=None 的錯誤路徑 | haiku / low |
| P1-5 | `similar.py:29` 硬編碼 `/tmp/phoxif_thumbs` → `tempfile.gettempdir()` 基底(對齊 routes.py:36 既有作法) | grep `"/tmp/` 在 phoxif/ = 0 | haiku / low |
| P1-6 | legacy 腳本 subprocess 裸奔(write_gps.py:37、organize.py:113、convert.py:30)→ 統一加 returncode 檢查 + timeout(參照 actions.py 既有模式) | 每處有 exiftool 缺席/失敗的測試或人工驗證紀錄 | sonnet / low |
| P1-7 | 兩套檔名日期解析(scanner.py:314 的 regex vs convert.py:38 的字串切片)統一到單一模組(如 `phoxif/api/dates.py`),行為以測試鎖定後合併 | 先加測試鎖現行為→合併→測試仍綠;grep 舊符號 0 命中 | sonnet / medium |
| P1-8 | README CLI 段落加 legacy 注記 + 指向 ADR-0007;`phoxif` console script 與 `python -m` 兩套入口的說明釐清 | README 與實際入口一致(逐條人工比對) | haiku / low |
| P1-9 | design.md 硬規則措辭更新(ADR-0006 決策 2)——**先說再改:需 Metal 核准後執行** | design.md 與 ADR-0006 無矛盾;改動 diff 先貼給 Metal | sonnet / low |

## RT — 2026-07-18 red-team 殘留(缺陷已知、尚未修,不得宣稱已解)

來源:三路 red-team 攻擊(K1 輸入模糊、K2 全管線、K3 狀態),完整證據在該次
session 報告。P0/多數 P1 已修(見檔尾已完成段);以下為誠實殘留。

| # | 項目 | 驗收條件 | 派工 |
|---|---|---|---|
| RT-1 | 中斷後無 resume 路徑(K2#A6/K3#2):F5/當機後只能重掃描重走各階段。已修掉「靜默毀損」(A15)與「無警告」(beforeunload),但 resume 本體未做——需 catalog-backed「未完成批次」清單 + 進入點 UI(架構級,先過 Metal) | 重整後可從 catalog 列出未完成 batch 並續走;含測試 | sonnet / high,先提案 |
| RT-2 | ingest 無數字進度(K2#A5):36.6GB 實測外推約 2 分鐘只有靜態文字(event loop 已修,期間縮圖仍可用)。需 per-file/bytes 進度(建議 SSE 或輪詢端點) | UI 顯示 n/N 檔或 bytes 進度;大批次實測 | sonnet / medium |
| RT-3 | 掃描進度條為裝飾性(K3#10):隨機遞增、95% 封頂,慢碟長掃描會停在 95%。與 RT-2 同批做真進度 | 進度反映實際掃描狀態 | 併入 RT-2 |
| RT-4 | 子目錄層級的權限缺損仍靜默(census 只驗 root 可讀;root 可讀但子目錄不可讀時,該子樹靜默缺席) | 掃描結果回報「無法讀取的子目錄清單」;測試 | sonnet / medium |

## P2 — 後續(時機到再做)

| # | 項目 | 觸發時機 |
|---|---|---|
| P2-1 | GitHub Actions CI(pytest + ruff + npm build + grep 閘門) | tests 站穩、P1-3 完成後 |
| P2-2 | orientation ONNX golden 樣本回歸測試(固定 8 張已知方向圖→斷言輸出) | 下次動 orientation_ai.py 前 |
| P2-3 | routes.py(1039 行)按 domain 拆 sub-routers | Phase 5 GUI 駕駛艙化時一併 |
| P2-4 | websockets 依賴:design.md 說要進度推送但 code 無 WebSocket 路由——實作或移除依賴,二選一 | Phase 5 |

## 已完成

- (2026-07-07)tests/ 骨架 + 22 seed tests + pytest dev dep — 本 session 建立
