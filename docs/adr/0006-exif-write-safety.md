# ADR-0006:EXIF 寫入單一 choke point + grep 閘門

狀態:Accepted(2026-07-07)

## 背景

design.md 硬規則寫「`exiftool -overwrite_original` NEVER used」,但
`phoxif/api/actions.py:190,261,308,329` 有 4 處直接使用;`_rotate_pillow`
(actions.py:300-303)甚至無 temp 檔直接原地覆寫。legacy CLI 另有 4 處:
`convert.py:108,127,138`(寫自產轉檔輸出,非使用者原檔)與
`write_gps.py:66`(寫使用者原檔;處置時程見 ADR-0007)。全 repo 基線共 8 處。文件與程式碼矛盾時,
AI 開發者會模仿程式碼,違規繁殖。需要一個既安全又可機器稽查的收斂點。

## 決策

1. 新增 `phoxif/api/exif_writer.py`,**全 repo 唯一**允許執行「對使用者
   檔案寫入 metadata / 覆寫檔案內容」的模組。統一流程:
   ```
   copy 原檔到同目錄 temp(保留 mtime)
   → 對 temp 寫入(exiftool 或 Pillow)
   → read-back 驗證(寫入的 tag 讀得回來、檔案可解碼開啟)
   → os.replace(temp, 原檔)   # 同檔案系統內原子替換
   → 寫 operation log(undo 用:記 tag 原值)
   → 失敗任一步:刪 temp,原檔一個 byte 都沒動過
   ```
   在這個流程裡,對 temp 檔用 `-overwrite_original` 是安全且正確的
   (省掉 exiftool 自己的 `_original` 備份;真正的保護來自 temp+replace)。
2. **design.md 硬規則措辭更新**(待 Metal 核准,屬「先說再改」):
   從「禁止 `-overwrite_original`」精緻化為
   「禁止對使用者原檔 in-place 寫入;一切寫入必經 exif_writer」。
   原規則的立法意圖(不毀原檔)不變,禁的對象從 flag 改為行為。
3. **grep 閘門**(權威指令與分層基準見 `docs/quality.md` 指令區 G1a/G1b):
   新程式區 `phoxif/api/` 在 P0-4 後必須為 0;全 repo 基線 8 只准變少;
   legacy 尾款(write_gps.py:66)在 ADR-0007 Phase 3 吸收時歸零。
4. 遷移順序:先建 exif_writer + 測試(寫入→read-back→undo 全綠),
   再把 actions.py 4 處與 `_rotate_pillow` 改為呼叫它,最後 legacy CLI
   (ADR-0007)。每改一處跑全套測試。

## 理由

1. 「單一 choke point + grep 閘門」把安全從自由心證變成機器可判定,
   這是弱模型長期開發下唯一守得住的形態。
2. temp+verify+atomic replace 讓「寫壞檔案」在物理上不可能波及原檔
   (電源中斷最壞留下一個 temp 垃圾檔)。
3. 承認 `-overwrite_original` 在 temp 流程內無害,避免規則與實務打架
   ——打架的規則沒人遵守。

## 被否決的替代方案

- **維持 exiftool 預設行為(產生 `_original` 備份檔)**:歷史上已被
  否決(memory:「No `_original` file bloat」),備份檔汙染資料夾且
  無索引,不如 catalog/log 記原值。
- **每個呼叫點各自實作 temp+replace**:與現狀等價,無法稽查,必然漂移。

## 重估訊號

- exiftool 逐檔 subprocess 在萬張級 Enrich 變成瓶頸:在 exif_writer
  **內部**改用 `-stay_open` batch 模式,介面不變,不算推翻本 ADR。

## 正例/反例

- ✅ 正例:orientation fix 呼叫 `exif_writer.write_tags(path, {"Orientation": 1})`,
  writer 內部 temp+replace+log;actions.py 不再出現 exiftool 寫入指令。
- ❌ 反例:新功能「批次改標題」直接 `subprocess.run(["exiftool", "-Title=x",
  "-overwrite_original", path])`——grep 閘門會抓到,review 必 FAIL。
