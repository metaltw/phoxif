import React from 'react';
import type { DedupePair, DedupeSummary, IntakeIngestSummary, PendingTrashItem, ScanResult, TrashExecutionSummary } from '../types';

interface ReviewScreenProps {
  scanResult: ScanResult;
  ingesting: boolean;
  ingestSummary: IntakeIngestSummary | null;
  ingestError: string | null;
  dedupeSummary: DedupeSummary | null;
  deduping: boolean;
  dedupeError: string | null;
  resolvingPair: string | null;
  pendingTrash: PendingTrashItem[];
  trashing: boolean;
  trashSummary: TrashExecutionSummary | null;
  onIngest: () => void;
  onDedupe: () => void;
  onResolvePair: (batchId: string, pair: DedupePair, keepSha256: string | null) => void;
  onApproveTrash: () => void;
  onReset: () => void;
  formatSize: (bytes: number) => string;
}

export function ReviewScreen({
  scanResult,
  ingesting,
  ingestSummary,
  ingestError,
  dedupeSummary,
  deduping,
  dedupeError,
  resolvingPair,
  pendingTrash,
  trashing,
  trashSummary,
  onIngest,
  onDedupe,
  onResolvePair,
  onApproveTrash,
  onReset,
  formatSize,
}: ReviewScreenProps): React.JSX.Element {
  const ingestFailed = ingestSummary !== null && ingestSummary.failures.length > 0;
  const ingestPartiallyCompleted = ingestFailed && ingestSummary.batches.length > 0;
  const dedupeTotals = dedupeSummary?.results.reduce(
    (totals, result) => ({
      exact: totals.exact + result.exact_groups.length,
      auto: totals.auto + result.auto_groups.length,
      review: totals.review + result.review_pairs.length,
      protected: totals.protected + result.burst_pairs.length + result.protected_edits.length,
    }),
    { exact: 0, auto: 0, review: 0, protected: 0 },
  );
  const reviewQueue = dedupeSummary?.results.flatMap(result =>
    result.review_pairs.map(pair => ({ batchId: result.batch_id, pair })),
  ) ?? [];
  const dedupeFailed = (dedupeSummary?.failures.length ?? 0) > 0;
  const pendingDecisionCount = reviewQueue.length + pendingTrash.length;
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
              ? trashSummary && trashSummary.completed > 0
                ? `整理完成 · ${trashSummary.completed} 筆批准項目已進系統垃圾桶`
                : ingestSummary.complete
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
              <p>{trashSummary && trashSummary.completed > 0
                ? '已依你的明確批准，將確認的重複檔移到系統垃圾桶；未批准的來源檔保持原位。'
                : '目前只建立工作副本與追蹤紀錄；不刪來源、不寫 metadata、不碰 NAS。'}</p>
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

        {dedupeSummary && dedupeTotals && (
          <section className="dedupe-result" aria-label="跨來源去重結果">
            <h2>跨來源比對完成</h2>
            <p>只寫入 catalog 判斷；沒有刪除任何來源或工作副本。</p>
            <div className="dedupe-result-grid">
              <div><strong>{dedupeTotals.exact}</strong><span>同一內容</span></div>
              <div><strong>{dedupeTotals.auto}</strong><span>高信心壓縮版</span></div>
              <div><strong>{dedupeTotals.review}</strong><span>需要你判斷</span></div>
              <div><strong>{dedupeTotals.protected}</strong><span>連拍／編輯版雙留</span></div>
            </div>
            {dedupeTotals.review > 0 && (
              <div className="manual-review-note">有 {dedupeTotals.review} 組保持未決；你未選擇前，不會送進垃圾桶。</div>
            )}
          </section>
        )}

        {reviewQueue.length > 0 && (
          <section className="pair-review" aria-label="需要人工判斷的相似照片">
            <div className="section-title-row">
              <div>
                <h2>這些相似照片需要你決定</h2>
                <p>沒有預選刪除項目。可保留其中一張，也可明確選擇兩張都留。</p>
              </div>
              <span className="read-only-badge">{reviewQueue.length} 組待判斷</span>
            </div>
            {reviewQueue.map(({ batchId, pair }) => (
              <article className="pair-review-card" key={`${batchId}-${pair.id}`}>
                <div className="pair-distance">視覺距離 {pair.distance} · {pair.reason}</div>
                <div className="pair-files">
                  {pair.files.map(file => (
                    <div className="pair-file" key={file.sha256}>
                      <img src={`/api/thumbnail?path=${encodeURIComponent(file.path)}`} alt={file.name} />
                      <strong>{file.name}</strong>
                      <small>{file.width ?? '?'} × {file.height ?? '?'} · {formatSize(file.size)}</small>
                      <small>{file.native_date ? '有原生拍攝日期' : '無原生拍攝日期'} · {file.has_gps ? '有 GPS' : '無 GPS'}</small>
                      <button
                        className="btn-secondary"
                        onClick={() => onResolvePair(batchId, pair, file.sha256)}
                        disabled={resolvingPair === pair.id}
                      >保留這張</button>
                    </div>
                  ))}
                </div>
                <button
                  className="btn-keep-both"
                  onClick={() => onResolvePair(batchId, pair, null)}
                  disabled={resolvingPair === pair.id}
                >兩張都留</button>
              </article>
            ))}
          </section>
        )}

        {dedupeSummary && reviewQueue.length === 0 && pendingTrash.length > 0 && (
          <section className="trash-approval" aria-label="待移到系統垃圾桶">
            <div>
              <h2>最後一步：批准重複檔案進系統垃圾桶</h2>
              <p>共 {pendingTrash.length} 筆。rescue 模式只動工作副本；inbox 模式會處理收件資料夾中的重複檔。原始來源證據仍留在 catalog。</p>
            </div>
            <div className="trash-list">
              {pendingTrash.map(item => (
                <div key={item.operation_id}>
                  <strong>{item.reason === 'archived_reunion' ? '收藏庫已有相同照片' : '已確認較差版本'}</strong>
                  <small>{item.names.join('、')}</small>
                </div>
              ))}
            </div>
            <button className="btn-danger-outline" onClick={onApproveTrash} disabled={trashing}>
              {trashing ? '正在移到系統垃圾桶…' : `批准 ${pendingTrash.length} 筆移到系統垃圾桶`}
            </button>
          </section>
        )}

        {trashSummary && pendingTrash.length === 0 && (
          <div className="trash-complete" role="status">
            ✓ 已完成 {trashSummary.completed} 筆垃圾桶操作；{trashSummary.failed} 筆失敗。
          </div>
        )}

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
            <button
              className="btn-secondary"
              onClick={ingestFailed ? onIngest : dedupeSummary ? (dedupeFailed ? onDedupe : onReset) : onDedupe}
              disabled={ingesting || deduping || (dedupeSummary !== null && pendingDecisionCount > 0)}
            >
              {ingestFailed
                ? (ingesting ? '正在安全重試…' : '安全重試')
                : dedupeSummary
                  ? pendingDecisionCount > 0
                    ? `先完成上方 ${pendingDecisionCount} 項決定`
                    : dedupeFailed
                      ? '安全重試比對'
                      : '整理下一批'
                  : deduping
                    ? '正在跨來源比對…'
                    : '檢查重複與例外 →'}
            </button>
            {dedupeError && <small>{dedupeError}</small>}
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
