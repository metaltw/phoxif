import React from 'react';
import type { Screen, ScanResult, ThumbState } from '../types';

interface ReviewScreenProps {
  scanResult: ScanResult;
  reviewedCategories: Set<string>;
  dupStates: Map<number, ThumbState[]>;
  simStates: Map<number, ThumbState[]>;
  onNavigate: (screen: Screen) => void;
  formatSize: (bytes: number) => string;
}

interface SelectionSummary {
  count: number;
  size: number;
}

function selectedTrash(
  groups: Array<{ id: number; files: Array<{ size: number }> }>,
  statesByGroup: Map<number, ThumbState[]>,
): SelectionSummary {
  let count = 0;
  let size = 0;

  for (const group of groups) {
    const states = statesByGroup.get(group.id);
    if (!states) continue;
    states.forEach((state, index) => {
      if (state === 'trash') {
        count += 1;
        size += group.files[index].size;
      }
    });
  }

  return { count, size };
}

export function ReviewScreen({
  scanResult,
  reviewedCategories,
  dupStates,
  simStates,
  onNavigate,
  formatSize,
}: ReviewScreenProps): React.JSX.Element {
  const duplicateCopies = scanResult.duplicates.reduce(
    (sum, group) => sum + Math.max(0, group.files.length - 1),
    0,
  );
  const similarFiles = scanResult.similar_groups.reduce(
    (sum, group) => sum + group.files.length,
    0,
  );
  const duplicateSelection = selectedTrash(scanResult.duplicates, dupStates);
  const similarSelection = selectedTrash(scanResult.similar_groups, simStates);
  const selectedCount = duplicateSelection.count + similarSelection.count;
  const selectedSize = duplicateSelection.size + similarSelection.size;
  const hasReviewItems = duplicateCopies > 0 || similarFiles > 0;
  const modeCopy = scanResult.mode === 'inbox'
    ? '這次以 LINE／WeChat 照片為主；日期不確定的照片會留下來確認，不會猜了就寫進檔案。'
    : '這次先把各處照片合併盤點；相同副本只留一份，看起來相似的照片交給你決定。';

  return (
    <div className="screen">
      <main className="intake-review">
        <header className="intake-review-head">
          <div>
            <div className="result-status"><span>✓</span> 唯讀掃描完成 · 尚未更動任何檔案</div>
            <h1>這批照片，我們看清楚了</h1>
            <p>{modeCopy}</p>
          </div>
          <button className="btn-secondary" onClick={() => onNavigate('scan')}>重新選擇</button>
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
              <h2 id="decision-title">只有這些需要你看</h2>
              <p>能確定的由 phoxif 處理；可能誤判的，一律先問你。</p>
            </div>
          </div>
          <div className="decision-grid">
            <button
              className={`decision-card${reviewedCategories.has('duplicates') ? ' reviewed' : ''}`}
              disabled={duplicateCopies === 0}
              onClick={() => onNavigate('duplicates')}
            >
              <span className="decision-icon">＝</span>
              <span className="decision-copy">
                <strong>完全相同的照片</strong>
                {duplicateCopies > 0 ? (
                  <small>{scanResult.duplicates.length} 組、{duplicateCopies} 個多出的副本；先讓你確認保留哪一份。</small>
                ) : (
                  <small>沒有找到完全相同的副本。</small>
                )}
              </span>
              <span className="decision-action">{duplicateCopies > 0 ? '檢查 →' : '✓'}</span>
            </button>

            <button
              className={`decision-card${reviewedCategories.has('similar') ? ' reviewed' : ''}`}
              disabled={similarFiles === 0}
              onClick={() => onNavigate('similar')}
            >
              <span className="decision-icon">◫</span>
              <span className="decision-copy">
                <strong>看起來很像的照片</strong>
                {similarFiles > 0 ? (
                  <small>{scanResult.similar_groups.length} 組、{similarFiles} 張；永遠不會自動刪除。</small>
                ) : (
                  <small>沒有需要人工判斷的相似照片。</small>
                )}
              </span>
              <span className="decision-action">{similarFiles > 0 ? '檢查 →' : '✓'}</span>
            </button>
          </div>
        </section>

        {selectedCount > 0 ? (
          <div className="result-next-bar">
            <div>
              <strong>已選 {selectedCount} 個副本</strong>
              <span>預計移到系統垃圾桶，可釋放 {formatSize(selectedSize)}；執行前還會再確認一次。</span>
            </div>
            <button className="btn-execute" onClick={() => onNavigate('confirm')}>查看執行方案 →</button>
          </div>
        ) : (
          <div className="result-honesty-bar">
            <span className="honesty-icon">i</span>
            <div>
              <strong>{hasReviewItems ? '請先檢查上面的例外' : '盤點完成，原始檔案保持不動'}</strong>
              <span>歸檔到相簿／NAS 與補日期會在後續階段接上；目前不會假裝已經整理完成。</span>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
