import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { IntakeMode, ScanResult } from '../types';
import { pickFolder, scanSources } from '../api';

interface ScanScreenProps {
  onComplete: (result: ScanResult) => void;
  initialSources?: string[];
}

// Clean up paste artifacts: file:// URIs, wrapping quotes, shell-escaped spaces.
function normalizeFolderInput(raw: string): string {
  let path = raw.trim();
  if (path.startsWith('file://')) {
    const rest = path.slice('file://'.length);
    try {
      path = decodeURIComponent(rest);
    } catch {
      path = rest;
    }
  }
  if ((path.startsWith('"') && path.endsWith('"'))
    || (path.startsWith("'") && path.endsWith("'"))) {
    path = path.slice(1, -1);
  }
  return path.replace(/\\ /g, ' ').trim();
}

function describeScanError(raw: string): string {
  const missing = raw.match(/^Path not found: (.+)$/);
  if (missing) {
    return `找不到資料夾：${missing[1]}。請確認路徑拼字，或外接硬碟是否已連接。`;
  }
  const file = raw.match(/^Not a folder: (.+)$/);
  if (file) {
    return `這是單一檔案：${file[1]}。請改貼「包含照片的資料夾」路徑。`;
  }
  const denied = raw.match(/^Permission denied: (.+)$/);
  if (denied) {
    return `沒有權限讀取：${denied[1]}。請調整資料夾權限，或在「系統設定 → 隱私權與安全性」允許存取後重試。`;
  }
  const overlap = raw.match(/^Sources overlap: (.+) is inside (.+)$/);
  if (overlap) {
    return `來源互相重疊：「${overlap[1]}」已包含在「${overlap[2]}」裡面。請移除其中一個，以免同一張照片被算成兩份。`;
  }
  return raw || '無法讀取來源，請確認資料夾仍然可用。';
}

const SCAN_MESSAGES = [
  '正在讀取照片與影片資訊…',
  '正在比對不同來源裡的相同檔案…',
  '正在尋找可能的聊天軟體壓縮版本…',
  '正在檢查遺失或不可靠的日期…',
  '正在準備整理摘要…',
];

export function ScanScreen({ onComplete, initialSources }: ScanScreenProps): React.JSX.Element {
  const [scanning, setScanning] = useState(false);
  const [mode, setMode] = useState<IntakeMode>('rescue');
  const [sources, setSources] = useState<string[]>(initialSources ?? []);
  const [manualPath, setManualPath] = useState('');
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const scanRequestedRef = useRef(false);

  const startScan = useCallback(() => {
    if (sources.length === 0 || scanRequestedRef.current) return;
    scanRequestedRef.current = true;
    setError(null);
    setScanning(true);
    setProgress(0);
    setMessage(SCAN_MESSAGES[0]);

    scanSources(sources, mode)
      .then((result) => {
        setProgress(100);
        setMessage('掃描完成');
        setTimeout(() => onComplete(result), 400);
      })
      .catch((err) => {
        console.error('Source scan failed:', err);
        scanRequestedRef.current = false;
        setScanning(false);
        setError(describeScanError(err instanceof Error ? err.message : ''));
      });
  }, [mode, onComplete, sources]);

  const addSource = useCallback((path: string) => {
    const normalized = normalizeFolderInput(path);
    if (!normalized) return;
    if (sources.includes(normalized)) {
      setError(`這個資料夾已經在清單中，不會重複加入：${normalized}`);
      setManualPath('');
      return;
    }
    setSources((current) => current.includes(normalized) ? current : [...current, normalized]);
    setManualPath('');
    setError(null);
  }, [sources]);

  // Warn before leaving while a scan is in flight — a reload silently discards it.
  useEffect(() => {
    if (!scanning) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [scanning]);

  const handleBrowse = useCallback(async () => {
    const path = await pickFolder();
    if (path) {
      addSource(path);
    }
  }, [addSource]);

  // Progress animation
  useEffect(() => {
    if (!scanning) return;
    let p = 0;
    intervalRef.current = setInterval(() => {
      p += Math.random() * 12 + 4;
      if (p > 95) p = 95;
      setProgress(prev => prev >= 100 ? 100 : Math.min(p, 95));
      setMessage(SCAN_MESSAGES[Math.min(Math.floor(p / 21), SCAN_MESSAGES.length - 1)]);
    }, 450);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [scanning]);

  if (scanning) {
    return (
      <div className="screen-center">
        <div className="scanning-overlay">
          <div className="scan-spinner" />
          <div className="scan-progress">
            <div className="scan-pbar">
              <div className="scan-pfill" style={{ width: `${progress}%` }} />
            </div>
            <div className="scan-ptext">{message}</div>
            <div className="scan-source-count">
              正在安全讀取 {sources.length} 個來源；原始檔不會被修改
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-center">
      <div className="inbox-start">
        <div className="inbox-eyebrow">PHOXIF PHOTO INBOX</div>
        <h1>把散落的照片，安全帶回同一個家</h1>
        <p className="inbox-lead">
          加入舊硬碟、手機備份，或 LINE／WeChat 匯出資料夾。先看清楚，再決定怎麼整理。
        </p>

        <div className="mode-grid" role="radiogroup" aria-label="整理方式">
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'rescue'}
            className={`mode-card${mode === 'rescue' ? ' selected' : ''}`}
            onClick={() => setMode('rescue')}
          >
            <span className="mode-choice">{mode === 'rescue' ? '✓ 目前選擇' : '選擇此模式'}</span>
            <span className="mode-icon">◫</span>
            <strong>整理多年舊照片</strong>
            <span>一次加入多台電腦、硬碟與手機備份，跨來源找出重複。</span>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={mode === 'inbox'}
            className={`mode-card${mode === 'inbox' ? ' selected' : ''}`}
            onClick={() => setMode('inbox')}
          >
            <span className="mode-choice">{mode === 'inbox' ? '✓ 目前選擇' : '選擇此模式'}</span>
            <span className="mode-icon">⇩</span>
            <strong>整理 LINE／WeChat 新照片</strong>
            <span>辨認聊天軟體壓縮版，盡量配回原圖與正確日期。</span>
          </button>
        </div>

        <section className="source-box" aria-label="照片來源">
          <div className="source-box-head">
            <div>
              <h2>{mode === 'rescue' ? '這次照片放在哪裡？' : '聊天照片匯出到哪裡？'}</h2>
              <p>{mode === 'rescue' ? '可以連續加入多個資料夾，稍後一起比對。' : '可分別加入 LINE、WeChat 或其他聊天軟體資料夾。'}</p>
            </div>
            <button type="button" className="btn-add-source" onClick={handleBrowse}>
              ＋ 選擇資料夾
            </button>
          </div>

          {sources.length === 0 ? (
            <button type="button" className="empty-source" onClick={handleBrowse}>
              <span>＋</span>
              <strong>加入第一個照片資料夾</strong>
              <small>現在只會讀取，不會移動、改名或刪除任何檔案</small>
            </button>
          ) : (
            <>
              <div className="source-state" role="status">
                <span>✓</span>
                <div>
                  <strong>已加入 {sources.length} 個照片資料夾</strong>
                  <small>尚未掃描。確認下方路徑後，按藍色「開始掃描」。</small>
                </div>
              </div>
              <div className="source-list">
                {sources.map((source, index) => (
                  <div className="source-row" key={source}>
                    <span className="source-number">{index + 1}</span>
                    <span className="source-path">{source}</span>
                    <span className="source-pending">等待掃描</span>
                    <button
                      type="button"
                      className="source-remove"
                      aria-label={`移除 ${source}`}
                      onClick={() => setSources(current => current.filter(item => item !== source))}
                    >
                      移除
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="manual-source">
            <input
              type="text"
              value={manualPath}
              onChange={(event) => setManualPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') addSource(manualPath);
              }}
              placeholder="也可以貼上資料夾路徑"
            />
            <button type="button" onClick={() => addSource(manualPath)} disabled={!manualPath.trim()}>
              加入
            </button>
          </div>

          {error && <div className="source-error">{error}</div>}
        </section>

        <div className="start-actions">
          <div className="read-only-note">
            <span>✓</span>
            <div><strong>第一步完全唯讀</strong><small>掃描完成後，所有動作都會先列給你確認。</small></div>
          </div>
          <button type="button" className="btn-start-census" onClick={startScan} disabled={sources.length === 0}>
            {sources.length === 0 ? '請先加入照片資料夾' : `開始掃描 ${sources.length} 個資料夾 →`}
          </button>
        </div>
      </div>
    </div>
  );
}
