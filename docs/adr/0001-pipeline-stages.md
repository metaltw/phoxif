# ADR-0001:五階段管線,去重先於補值,歸檔後唯讀

狀態:Accepted(2026-07-07,Fable 5 開釋 session,Metal 訪談定案優先序)

## 背景

phoxif 的實際任務是:散落多台電腦的照片/影片 → 收集 → 去重 → 補齊
metadata → 歸檔到 NAS → Immich 索引。現有程式碼是「單資料夾工具」的
集合(scan/rename/orientation/classify 各自為政),沒有明確的階段順序;
「WeChat/LINE 日期補齊」功能尚未存在,做的時候必須先決定它在管線的哪一站。

## 決策

管線固定為五階段,順序不可對調:

```
1. Ingest   收集:從來源(各電腦/資料夾)匯入,當下記錄證據
2. Dedupe   去重:先精確(sha256)後近似(perceptual hash)
3. Enrich   補值:日期(ADR-0004)→ GPS(ADR-0005),寫入帶溯源標記
4. Review   審核:GUI dry-run 檢視與確認(沿用既有五步流程)
5. Archive  歸檔:落入 NAS 穩定樹;歸檔後該檔案「唯讀」——
            phoxif 永不再改名/移動/改寫它(ADR-0003 的前提)
```

硬規則兩條:

- **Dedupe 必在 Enrich 之前**。
- **Ingest 當下必須記錄證據**:來源機器標籤、原始完整路徑、原始檔名、
  原始 mtime/birthtime,存入 catalog(ADR-0002)。收集用的複製指令必須
  保留 mtime(`rsync -a` / `cp -p`)。

## 理由

1. **近重複組是 metadata 恢復通道**:WeChat/LINE 轉存檔常是「收藏裡
   別處存在的原檔」的再壓縮版。先跑 dedupe,phash 把轉存檔和帶完整
   EXIF 的原檔配成一組 → 留原檔、丟轉存檔,**根本不需要估日期**。
   反過來先 Enrich,就會把估計日期寫進一堆其實該被丟掉的複本——
   浪費工,還污染資料。
2. **證據會被搬運銷毀**:mtime、原始路徑(資料夾名常含日期/地點線索)
   在複製、下載、雲端同步後隨時可能消失。不在 Ingest 當下記錄,
   之後永遠拿不回來。這個專案本質是資料救援,救援第一課是保全現場。
3. **歸檔後唯讀**是 Immich external library 的硬需求:Immich 以路徑識別
   external library 資產,改名/移動 = Immich 眼中刪一張新增一張,
   timeline/album 關聯斷裂(官方已知行為,本地未實測;workflow.md:150
   既有警語,升級為鐵律)。

## 被否決的替代方案

- **維持現狀(GUI 內單資料夾一次做完)**:無法跨機器去重,狀態不持久。
- **先補日期再去重**(按功能熱度排序):見理由 1,順序錯誤會污染資料。
- **歸檔後允許 phoxif 繼續整理 NAS 樹**:會破壞 Immich 資產識別;
  歸檔後的修正一律走「Immich 內操作」或「catalog 標記 + 下一批修正」。

## 重估訊號

- 實際收集後統計:若近重複率 <5%(catalog 可查),dedupe-first 的
  價值降低,可考慮 Dedupe/Enrich 並行以縮短流程——但仍不准反轉。
- Immich 若改版為以內容 hash 識別資產(而非路徑),「歸檔後唯讀」可放寬。

## 正例/反例(給實作時對照)

- ✅ 正例:mmexport 檔在 Dedupe 階段與 IMG_5023.HEIC 配對(phash 距離 ≤ 門檻)
  → 標記轉存檔為 duplicate、繼承組;Enrich 階段跳過它。
- ❌ 反例:掃到 mmexport 檔就立刻解析檔名 epoch 寫入 DateTimeOriginal,
  之後 dedupe 才發現原檔存在 → 白寫,且垃圾桶裡多了一個「被改過的檔」。
