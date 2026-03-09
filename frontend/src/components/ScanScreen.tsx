import React, { useState, useEffect, useRef, useCallback } from 'react';
import type { ScanResult } from '../types';
import { scanFolder, pickFolder } from '../api';

interface ScanScreenProps {
  onComplete: (result: ScanResult) => void;
}

const SCAN_MESSAGES = [
  'Reading file metadata...',
  'Checking duplicates (MD5)...',
  'Detecting similar photos...',
  'Analyzing video codecs...',
  'Reverse geocoding GPS...',
  'Building results...',
];

export function ScanScreen({ onComplete }: ScanScreenProps): React.JSX.Element {
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('');
  const [folderPath, setFolderPath] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const startScan = useCallback((path: string) => {
    if (!path.trim()) return;
    setError(null);
    setScanning(true);
    setProgress(0);
    setMessage(SCAN_MESSAGES[0]);

    scanFolder(path.trim())
      .then((result) => {
        setProgress(100);
        setMessage('Complete!');
        setTimeout(() => onComplete(result), 400);
      })
      .catch((err) => {
        console.error('Scan failed:', err);
        setScanning(false);
        setError(err instanceof Error ? err.message : 'Scan failed. Is the backend running?');
      });
  }, [onComplete]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      startScan(folderPath);
    }
  }, [folderPath, startScan]);

  const handleBrowse = useCallback(async () => {
    const path = await pickFolder();
    if (path) {
      setFolderPath(path);
      setError(null);
    }
  }, []);

  // Drag & drop handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);

    // Try to get folder path from dropped items
    const items = e.dataTransfer.items;
    if (items && items.length > 0) {
      const item = items[0];
      // webkitGetAsEntry gives us the path for local files
      const entry = item.webkitGetAsEntry?.();
      if (entry?.isDirectory) {
        // webkitGetAsEntry fullPath is a virtual path (e.g. "/FolderName"), not a real FS path.
        // Use just the name and let the backend resolve it.
        setFolderPath(entry.name);
        return;
      }
    }

    // Fallback: try files
    const files = e.dataTransfer.files;
    if (files.length > 0) {
      // Can't get directory path from File API, show hint
      setError('Drag a folder from Finder, or type the path below.');
    }
  }, []);

  // Progress animation
  useEffect(() => {
    if (!scanning) return;
    let p = 0;
    intervalRef.current = setInterval(() => {
      p += Math.random() * 15 + 5;
      if (p > 95) p = 95;
      setProgress(prev => prev >= 100 ? 100 : Math.min(p, 95));
      setMessage(SCAN_MESSAGES[Math.min(Math.floor(p / 18), SCAN_MESSAGES.length - 1)]);
    }, 300);
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
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="screen-center">
      <div className="scan-wrap">
        <h1>Organize Your Photos</h1>
        <p style={{ color: 'var(--dim)', fontSize: '15px', marginBottom: '28px' }}>
          Enter a folder path or drag it from Finder
        </p>

        {/* Drop zone */}
        <div
          className={`drop-zone ${dragOver ? 'drop-zone-active' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          style={dragOver ? { borderColor: 'var(--accent)', background: 'var(--accent-dim)' } : undefined}
        >
          <div className="dz-icon">📂</div>
          <div className="dz-text">Drop a folder here</div>
        </div>

        {/* Path input */}
        <div style={{
          display: 'flex',
          gap: '8px',
          width: '480px',
          margin: '16px auto',
        }}>
          <input
            ref={inputRef}
            type="text"
            value={folderPath}
            onChange={(e) => { setFolderPath(e.target.value); setError(null); }}
            onKeyDown={handleKeyDown}
            placeholder="~/Photos/2026-March-Trip"
            style={{
              flex: 1,
              padding: '10px 14px',
              background: 'var(--bg3)',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              color: 'var(--bright)',
              fontSize: '14px',
              fontFamily: "'SF Mono', 'Fira Code', monospace",
              outline: 'none',
            }}
            onFocus={(e) => { (e.target as HTMLInputElement).style.borderColor = 'var(--accent)'; }}
            onBlur={(e) => { (e.target as HTMLInputElement).style.borderColor = 'var(--border)'; }}
          />
          <button
            onClick={handleBrowse}
            style={{
              padding: '10px 14px',
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              color: 'var(--dim)',
              fontSize: '13px',
              cursor: 'pointer',
            }}
            title="Browse..."
          >
            📁
          </button>
        </div>

        {/* Scan button */}
        <button
          onClick={() => startScan(folderPath)}
          disabled={!folderPath.trim()}
          style={{
            display: 'block',
            margin: '0 auto 24px',
            padding: '12px 40px',
            borderRadius: '10px',
            border: 'none',
            background: folderPath.trim() ? 'var(--accent)' : 'var(--border)',
            color: folderPath.trim() ? 'var(--bg)' : 'var(--dim)',
            fontSize: '15px',
            fontWeight: 600,
            cursor: folderPath.trim() ? 'pointer' : 'default',
            transition: 'all 0.15s',
          }}
        >
          Scan
        </button>

        {/* Error message */}
        {error && (
          <div style={{
            textAlign: 'center',
            fontSize: '13px',
            color: 'var(--amber)',
            marginBottom: '16px',
          }}>
            {error}
          </div>
        )}

        {/* Safety banner */}
        <div className="safety-banner">
          <div className="sb-icon">🛡️</div>
          <div className="sb-text">
            <strong>Your files are safe.</strong><br />
            phoxif never permanently deletes files. Removals go to system Trash.<br />
            EXIF changes are logged for undo. Originals always preserved.
          </div>
        </div>
      </div>
    </div>
  );
}

