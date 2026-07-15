# phoxif 品質閘門與工作迴圈

本檔把全域 `~/.claude/rules/work-loop.md` 落地到本 repo:哪些驗證訊號
機器可判定、每輪 review 跑什麼、什麼叫完成。全域迴圈結構(PLAN→ACT→
REVIEW→IMPROVE、fresh verifier、熔斷)不重複,直接沿用。

## 1. 機器驗證訊號(依訊號強度排序,能跑的都要跑)

| 訊號 | 指令 | 通過標準 |
|---|---|---|
| Python 測試 | `uv run pytest -q` | 全綠(2026-07-07 基線:22 passed,<1s) |
| Python lint | `uv run ruff check .` | 0 errors(基線例外:main.py 2 個既有 F401,見 TODO;修掉前允許恰好這 2 個) |
| 前端型別+建置 | `cd frontend && npm run build` | exit 0(tsc + vite) |
| EXIF 安全閘門 | G1(下方指令區) | 見 G1 註解的分層基準 |
| 永久刪除閘門 | G2(下方指令區) | 見 G2 註解 |
| 公開 repo 個資閘門 | G3(下方指令區) | 輸出為空 |
| 報告規範 | `~/Documents/git/war_room/tools/report_check.sh .` | PASS |
| repo 衛生 | `~/Documents/git/war_room/tools/hygiene_check.sh .` | PASS(收工時) |

**grep 閘門指令區**(一律從這裡複製執行,markdown 表格內的 `\|` 跳脫會讓
grep 靜默假通過——2026-07-07 verifier 實證過的坑):

```bash
# G1a EXIF 安全(ADR-0006)— 新程式區,P0-4 完成後必須為空(基線 4:actions.py)
grep -rnI 'overwrite_original' phoxif/api/ | grep -v 'exif_writer'

# G1b EXIF 安全 — 全 repo 盤點,基線 8,只准變少、禁止新增
#   組成:api/actions.py 4 處(P0-4 歸零)+ legacy convert.py:108,127,138
#  (寫自產轉檔輸出,非使用者原檔)+ write_gps.py:66(寫使用者原檔,
#   ADR-0007 Phase 3 吸收時歸零)
grep -rnI 'overwrite_original' phoxif/ | grep -v 'api/exif_writer' | wc -l

# G2 永久刪除 — 基線 3 命中:convert.py:167 與 sorter.py:401(使用者檔案,
#   P0-1/P0-2 修復對象,修完歸零)+ convert.py:101(自產失敗輸出清理,允許)。
#   不得新增使用者檔案命中。注意:這是啟發式(抓不到 unlink(missing_ok=True)
#   等變體);紅線是「使用者檔案一律 send2trash」這條規則,不是這條 regex
grep -rnIE '(os\.remove|\.unlink\(\)|rmtree)' phoxif/

# G3 個資 — 輸出必須為空(個人路徑/座標/主機只准進 gitignored config.yaml)。
#   本檔(docs/quality.md)因含指令文字而自我排除,故本檔內禁止出現真實個資,
#   review 時人工過目
grep -rnIE '/Users/|/Volumes/|@gmail|100\.[0-9]+\.' \
  --include='*.py' --include='*.md' --include='*.ts' --include='*.tsx' \
  --include='*.yaml' . | grep -v -e .venv -e node_modules -e config.yaml \
  -e docs/quality.md
```

測試依賴本機 `exiftool`(整合測試會實呼);fixture 一律 `tmp_path` 動態生成,
禁止 commit 二進位樣本、禁止測試碰 repo 外真實照片。

## 2. 每輪 REVIEW 跑什麼(對照全域迴圈的觸發點)

- **每完成一個函式/模組**:`uv run pytest -q` + 該功能的新測試(先紅後綠)。
- **每完成一個驗證單元**:上表前四項。
- **收工前**:上表全部 + fresh-context verifier(全域規則,不自驗)。
- 改到 heuristic(classifier pattern、日期階梯、dedupe 規則)= **必先加 case**:
  這些是本專案的業務核心,回歸最致命、也最容易寫測試。

## 3. Definition of Done(按產出類型)

| 產出 | 全過才算完成 |
|---|---|
| 管線/後端功能 | 新測試先紅後綠;全套綠;lint 淨;grep 閘門淨;催生的 config 欄位已加進 `config.example.yaml`(不是只加 config.yaml) |
| 破壞性操作路徑(trash/覆寫/移動) | 上行 + dry-run 模式存在且預設;undo 路徑有測試;錯誤中斷不留半成品(temp 清理有 finally) |
| 前端功能 | `npm run build` 過;與後端的介面欄位在 `frontend/src/types.ts` 同步;人工走一次該畫面流程(附截圖或步驟描述) |
| 文件/ADR | 引用路徑逐一 `ls` 驗證;與既有 ADR 無矛盾(矛盾=回報,不默默選邊) |
| 報告 | 依 war_room repo-reports:`.html` self-contained + INDEX.md 更新 + index.html 重建 + report_check PASS |

## 4. 本 repo 特有的紅線(review 一票 FAIL 項)

1. 使用者檔案的永久刪除(`unlink`/`os.remove`/`rmtree`)——一律 send2trash。
2. 繞過 exif_writer 直接寫使用者檔案 metadata(ADR-0006;writer 建成前=
   新增任何 in-place 寫入)。
3. 估計值寫入不帶溯源標記(ADR-0004/0005)。
4. 對凍結模組(docstring 含 DEPRECATED)的功能性修改(ADR-0007)。
5. 個資(路徑/座標/主機名)出現在可 commit 的檔案(公開 repo)。
6. 對 Accepted ADR 提替代方案而無「重估訊號」證據。

## 5. 派工建議(對照全域 model-dispatch)

| 本 repo 常見任務 | 範本 | model/effort | 備註 |
|---|---|---|---|
| 加 heuristic + 測試(階梯新級、新檔名 pattern) | 範本 2 實作 | sonnet / medium | 驗收=先紅後綠+全套綠 |
| 批次套用已定模式(如 actions.py 改走 exif_writer) | 範本 2 → 降級 | sonnet 解首例,haiku 套其餘 | 每處改完跑測試 |
| catalog schema / 管線階段設計 | 範本 2+ADR | sonnet-opus / high | 動 schema 必附 migration+ADR 增補 |
| 真實照片批次的 dry-run 結果評估 | 範本 5 審查 | sonnet / medium | 抽查實檔,勿全信統計 |

## 6. 已知驗證缺口(誠實聲明,2026-07-07)

- 無 CI(無 `.github/workflows/`)——所有閘門靠 session 內自律跑。TODO P2。
- 前端無測試(僅 tsc/build)。E2E 留給 Phase 5 評估,現階段人工走流程。
- orientation ONNX 模型無 golden 樣本回歸測試(模型行為變化偵測不到)。TODO P2。
