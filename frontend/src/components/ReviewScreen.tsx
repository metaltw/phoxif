import React from 'react';
import type { IntakeIngestSummary, ScanResult } from '../types';

interface ReviewScreenProps {
  scanResult: ScanResult;
  ingesting: boolean;
  ingestSummary: IntakeIngestSummary | null;
  ingestError: string | null;
  onIngest: () => void;
  onReset: () => void;
  formatSize: (bytes: number) => string;
}

export function ReviewScreen({
  scanResult,
  ingesting,
  ingestSummary,
  ingestError,
  onIngest,
  onReset,
  formatSize,
}: ReviewScreenProps): React.JSX.Element {
  const ingestFailed = ingestSummary !== null && ingestSummary.failures.length > 0;
  const ingestPartiallyCompleted = ingestFailed && ingestSummary.batches.length > 0;
  const duplicateCopies = scanResult.duplicates.reduce(
    (sum, group) => sum + Math.max(0, group.files.length - 1),
    0,
  );
  const similarFiles = scanResult.similar_groups.reduce(
    (sum, group) => sum + group.files.length,
    0,
  );
  const modeCopy = scanResult.mode === 'inbox'
    ? '這次以 LINE／WeChat 照片為主；日期不確定的照片會留下來確認，不會猜了就寫進檔案。'
    : '這次先把各處照片合併盤點；相同內容只建立一個身分，原始來源保持不動。';
  const ingestTitle = scanResult.mode === 'rescue'
    ? '建立安全工作副本'
    : '登記這批收件照片';
  const ingestDescription = scanResult.mode === 'rescue'
    ? '每個檔案以 SHA-256 建立身分，複製後再次驗證；舊硬碟與原資料夾完全不動。'
    : '檔案留在收件資料夾，先建立永久紀錄；真正歸檔前仍會再讓你確認。';

  return (
    <div className="screen">
      <main className="intake-review">
        <header className="intake-review-head">
          <div>
            <div className="result-status"><span>✓</span> {ingestSummary
              ? ingestSummary.complete
                ? '安全登記完成 · 原始來源保持不動'
                : '部分來源已登記 · 原始來源保持不動'
              : '唯讀掃描完成 · 尚未更動任何檔案'}</div>
            <h1>這批照片，我們看清楚了</h1>
            <p>{modeCopy}</p>
          </div>
          <button className="btn-secondary" onClick={onReset} disabled={ingesting}>重新選擇</button>
        </header>

        <section className="result-metrics" aria-label="掃描摘要">
          <div className="result-metric primary">
            <strong>{scanResult.ready_to_collect.toLocaleString()}</strong>
            <span>張照片與影片</span>
            <small>準備納入整理</small>
          </div>
          <div className="result-metric">
            <strong>{duplicateCopies.toLocaleString()}</strong>
            <span>個相同副本</span>
            <small>{scanResult.duplicates.length} 組完全相同</small>
          </div>
          <div className="result-metric">
            <strong>{scanResult.messaging_files.toLocaleString()}</strong>
            <span>張聊天照片</span>
            <small>LINE／WeChat 也是正式照片</small>
          </div>
          <div className={`result-metric${scanResult.missing_dates > 0 ? ' attention' : ''}`}>
            <strong>{scanResult.missing_dates.toLocaleString()}</strong>
            <span>張日期待確認</span>
            <small>不確定就不自動修改</small>
          </div>
        </section>

        <section className="source-summary" aria-labelledby="source-summary-title">
          <div className="section-title-row">
            <div>
              <h2 id="source-summary-title">這次看了哪些地方</h2>
              <p>共 {scanResult.total_files.toLocaleString()} 個媒體檔，{formatSize(scanResult.total_size)}</p>
            </div>
            <span className="read-only-badge">唯讀</span>
          </div>
          <div className="source-summary-list">
            {scanResult.sources.map((source) => (
              <div className="source-summary-row" key={source.path} title={source.path}>
                <span className="source-folder-icon">⌂</span>
                <div className="source-summary-name">
                  <strong>{source.label}</strong>
                  <small>{source.path}</small>
                </div>
                <span>{source.photo_count.toLocaleString()} 張照片</span>
                <span>{source.video_count.toLocaleString()} 部影片</span>
                <span>{formatSize(source.total_size)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="decision-section" aria-labelledby="decision-title">
          <div className="section-title-row">
            <div>
              <h2 id="decision-title">phoxif 會怎麼處理</h2>
              <p>目前只建立工作副本與追蹤紀錄；不刪來源、不寫 metadata、不碰 NAS。</p>
            </div>
          </div>
          <div className="decision-grid">
            <div className="decision-card readonly">
              <span className="decision-icon">＝</span>
              <span className="decision-copy">
                <strong>完全相同的照片</strong>
                <small>{duplicateCopies > 0
                  ? `${scanResult.duplicates.length} 組、${duplicateCopies} 個副本會共用同一個內容身分；來源檔不刪。`
                  : '沒有找到完全相同的副本。'}</small>
              </span>
              <span className="decision-action">同一身分</span>
            </div>

            <div className="decision-card readonly">
              <span className="decision-icon">◫</span>
              <span className="decision-copy">
                <strong>看起來很像的照片</strong>
                <small>{similarFiles > 0
                  ? `${scanResult.similar_groups.length} 組、${similarFiles} 張只標記為候選；這一步絕不刪除。`
                  : '沒有需要人工判斷的相似照片。'}</small>
              </span>
              <span className="decision-action">只標記</span>
            </div>
          </div>
          <p className="intake-scope-note">
            此里程碑先保全照片／影片本體與來源證據；Live Photo 配對、AAE sidecar、聊天日期修復會在後續整理階段完成，現在不會假裝已處理。
          </p>
        </section>

        {ingestSummary ? (
          <div className={`intake-commit-bar${ingestFailed ? ' error' : ' success'}`} role="status">
            <span className="commit-icon">{ingestFailed ? '!' : '✓'}</span>
            <div>
              <strong>{ingestPartiallyCompleted
                ? '部分來源已完成，部分需要重試'
                : ingestFailed
                  ? '這批來源尚未完成'
                  : scanResult.mode === 'rescue'
                    ? '安全工作副本建立完成'
                    : '這批照片已登記'}</strong>
              <span>
                {ingestFailed
                  ? `${ingestSummary.batches.length} 個來源已安全完成，${ingestSummary.failures.length} 個失敗；重試不會重複建立副本。`
                  : <>
                      {ingestSummary.totals.new_files.toLocaleString()} 個新內容、
                      {ingestSummary.totals.new_sightings.toLocaleString()} 筆來源紀錄；
                      {scanResult.mode === 'rescue'
                        ? `確認 ${ingestSummary.totals.verified_staging.toLocaleString()} 個有效工作副本；本次新寫入 ${ingestSummary.totals.staged_files.toLocaleString()} 個。`
                        : '收件資料夾中的原檔保持原位。'}
                    </>}
              </span>
              {ingestFailed && (
                <small>{ingestSummary.failures.map((failure) => `${failure.label}: ${failure.error}`).join('；')}</small>
              )}
            </div>
            <button className="btn-secondary" onClick={ingestFailed ? onIngest : onReset} disabled={ingesting}>
              {ingestFailed ? (ingesting ? '正在安全重試…' : '安全重試') : '整理下一批'}
            </button>
          </div>
        ) : (
          <div className={`intake-commit-bar${ingestError ? ' error' : ''}`}>
            <span className="commit-icon">{ingestError ? '!' : '→'}</span>
            <div>
              <strong>{ingestError ? '尚未建立工作副本' : ingestTitle}</strong>
              <span>{ingestError ?? ingestDescription}</span>
            </div>
            <button className="btn-execute" onClick={onIngest} disabled={ingesting || scanResult.total_files === 0}>
              {ingesting ? '正在驗證與複製…' : `${ingestTitle} →`}
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
