# phoxif — 照片收件匣

phoxif 是 GUI-first 的照片／影片救援工具：把散落在舊電腦、目前使用中的資料夾，
以及 LINE／WeChat 轉存檔，安全整理成同一個可長期持有的主收藏庫。

它不是相簿瀏覽器，也不是一組要背指令的零散 CLI。正常使用流程只有一條：

1. 選擇歷史照片或聊天照片資料夾；
2. 先確保每張照片找得到、保得住：建立 catalog、內容身分與安全工作副本；
3. 依證據補日期，再保守補 GPS；無可靠證據就隔離，不猜；
4. 重複候選等 metadata 整理後再處理；不確定時預設兩張都留；
5. 截圖／文件等非照片只分流、不自動刪除；
6. 預覽每一筆歸檔目的地，明確批准後寫入唯讀日期樹；
7. 由 Immich External Library 掃描，不把照片主權交給特定相簿軟體。

產品判斷優先序固定為：**照片存在與可尋回 > 拍攝日期與 GPS > 重複整理 >
垃圾圖／非照片清理**。底層可先做非破壞性的雜湊比對來建立內容身分，但不代表
刪重複比 metadata 更重要；任何垃圾桶操作都排在日期與位置之後並另行批准。

## 安全承諾

- rescue 模式不修改舊硬碟／來源資料夾；metadata 只寫安全工作副本。
- inbox 模式在真正歸檔前保留收件原檔，任何清理都要另行批准。
- 所有估計日期與 GPS 都帶 `phoxif:*` 溯源標記並寫入 operation ledger。
- 重複檔只進系統垃圾桶，不永久刪除。
- Live Photo 的影像／短片以同一 basename 成組歸檔；同名 AAE 編輯 sidecar 一併保存。
- screenshot／文件等非照片另進 `_non_photos/`，不混入主要時間軸。
- 歸檔採暫存 copy → SHA-256 讀回校驗 → 唯讀發布；中斷後可安全續跑。
- 歸檔後不再改名、搬移或改寫，避免破壞 Immich external asset 身分。

## Requirements

- **Python 3.12+**
- **[exiftool](https://exiftool.org/)** — EXIF metadata read/write
- **[ffmpeg](https://ffmpeg.org/)** with HEVC support (VideoToolbox on macOS)

Install on macOS:

```bash
brew install exiftool ffmpeg
```

## Quick Start

1. Clone the repo and set up:

```bash
git clone https://github.com/user/phoxif.git
cd phoxif
cp config.example.yaml config.yaml
```

2. 編輯 `config.yaml`：至少設定 catalog、staging，以及確認後的
   `archive_root`。所有私人路徑與 GPS 都只放在這個 gitignored 檔案。

3. 啟動 app：

```bash
python main.py
```

開發模式使用 `python main.py --dev`。前端需 Node.js；Python 依賴由 `uv`
管理。

## 主收藏庫與 Immich

一般照片歸檔格式固定為 `YYYY/YYYY-MM/YYYYMMDD_HHMMSS[_n].ext`。Live Photo
與 AAE 共用 basename；非照片進 `_non_photos/<category>/`。詳細的唯讀 mount、
scan 與抽查步驟見 [Immich External Library 交接](docs/immich-external-library.md)。

架構與安全政策以 [ADR](docs/adr/README.md) 及
[管線設計](docs/pipeline-design.md) 為準。舊 CLI 已凍結，只供既有 workflow
相容，不是新使用者入口。

## Configuration

All personal paths, GPS coordinates, and settings live in `config.yaml` (gitignored).
See [`config.example.yaml`](config.example.yaml) for the full structure.

## License

[Apache License 2.0](LICENSE)
