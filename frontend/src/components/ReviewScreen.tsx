import React, { useEffect, useState } from 'react';
import type { ArchiveExecutionSummary, ArchivePlanSummary, DateExecutionSummary, DatePlanSummary, DedupePair, DedupeSummary, FileInfo, GpsExecutionSummary, GpsPlanSummary, IntakeIngestSummary, PendingTrashItem, ScanResult, TrashExecutionSummary } from '../types';

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
  datePlan: DatePlanSummary | null;
  dateExecution: DateExecutionSummary | null;
  dating: boolean;
  dateError: string | null;
  gpsPlan: GpsPlanSummary | null;
  gpsExecution: GpsExecutionSummary | null;
  gpsing: boolean;
  gpsError: string | null;
  archivePlan: ArchivePlanSummary | null;
  archiveExecution: ArchiveExecutionSummary | null;
  archiving: boolean;
  archiveError: string | null;
  onIngest: () => void;
  onDedupe: () => void;
  onResolvePair: (batchId: string, pair: DedupePair, keepSha256: string | null) => void;
  onApproveTrash: () => void;
  onDatePlan: () => void;
  onDateExecute: () => void;
  onGpsPlan: () => void;
  onGpsExecute: () => void;
  onArchivePlan: () => void;
  onArchiveExecute: () => void;
  onReset: () => void;
  formatSize: (bytes: number) => string;
}

interface PhotoProofCardProps {
  file: FileInfo;
  formatSize: (bytes: number) => string;
}

function PhotoProofCard({ file, formatSize }: PhotoProofCardProps): React.JSX.Element {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="photo-proof-card" title={file.path}>
      <div className={`photo-proof-image${failed ? ' failed' : ''}`}>
        {!failed && (
          <img
            src={`/api/thumbnail?path=${encodeURIComponent(file.path)}`}
            alt={file.name}
            loading="lazy"
            onError={() => setFailed(true)}
          />
        )}
        <span>{failed ? '無法預覽' : file.extension.replace('.', '').toUpperCase()}</span>
      </div>
      <figcaption>
        <strong>{file.name}</strong>
        <small>{failed
          ? '檔案仍完整保留，縮圖轉換失敗'
          : `${file.width && file.height ? `${file.width} × ${file.height} · ` : ''}${formatSize(file.size)}`}</small>
      </figcaption>
    </figure>
  );
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
  datePlan,
  dateExecution,
  dating,
  dateError,
  gpsPlan,
  gpsExecution,
  gpsing,
  gpsError,
  archivePlan,
  archiveExecution,
  archiving,
  archiveError,
  onIngest,
  onDedupe,
  onResolvePair,
  onApproveTrash,
  onDatePlan,
  onDateExecute,
  onGpsPlan,
  onGpsExecute,
  onArchivePlan,
  onArchiveExecute,
  onReset,
  formatSize,
}: ReviewScreenProps): React.JSX.Element {
  const [archiveConfirmed, setArchiveConfirmed] = useState(false);
  const [visiblePhotoCount, setVisiblePhotoCount] = useState(24);
  useEffect(() => {
    setArchiveConfirmed(false);
  }, [archivePlan?.plan_fingerprint]);
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
  const unresolvedPairCount = reviewQueue.length;
  const dateTotals = datePlan?.plans.reduce(
    (totals, plan) => ({
      native: totals.native + (plan.counts['keep-native'] ?? 0),
      estimated: totals.estimated + (plan.counts['write-estimated'] ?? 0),
      quarantine: totals.quarantine + (plan.counts.quarantine ?? 0),
      skipped: totals.skipped + (plan.counts.skip ?? 0),
    }),
    { native: 0, estimated: 0, quarantine: 0, skipped: 0 },
  );
  const quarantineItems = datePlan?.plans.flatMap(plan =>
    plan.items.filter(item => item.action === 'quarantine'),
  ) ?? [];
  const dateBatchFailures = [
    ...(datePlan?.failures ?? []),
    ...(dateExecution?.failures ?? []),
  ];
  const dateItemFailures = dateExecution?.results.flatMap(result =>
    result.results.filter(item => item.status === 'failed'),
  ) ?? [];
  const dateFailed = dateBatchFailures.length > 0 || dateItemFailures.length > 0
    || datePlan?.complete === false || dateExecution?.complete === false;
  const dateStageComplete = datePlan !== null && !dateFailed
    && dateExecution?.complete === true;
  const gpsTotals = gpsPlan?.plans.reduce(
    (totals, plan) => ({
      native: totals.native + (plan.counts['keep-native'] ?? 0),
      previous: totals.previous + (plan.counts['keep-backfilled'] ?? 0),
      mapped: totals.mapped + (plan.counts['write-mapped'] ?? 0),
      neighbor: totals.neighbor + (plan.counts['write-neighbor'] ?? 0),
      skipped: totals.skipped + (plan.counts.skip ?? 0),
    }),
    { native: 0, previous: 0, mapped: 0, neighbor: 0, skipped: 0 },
  );
  const gpsWrites = (gpsTotals?.mapped ?? 0) + (gpsTotals?.neighbor ?? 0);
  const gpsWrittenCount = gpsExecution?.results.reduce(
    (sum, result) => sum + result.results.filter(item => item.written === true).length,
    0,
  ) ?? 0;
  const gpsBatchFailures = [
    ...(gpsPlan?.failures ?? []),
    ...(gpsExecution?.failures ?? []),
  ];
  const gpsItemFailures = gpsExecution?.results.flatMap(result =>
    result.results.filter(item => item.status === 'failed'),
  ) ?? [];
  const gpsFailed = gpsBatchFailures.length > 0 || gpsItemFailures.length > 0
    || gpsPlan?.complete === false || gpsExecution?.complete === false;
  const gpsWriteItems = gpsPlan?.plans.flatMap(plan =>
    plan.items.filter(item => item.action === 'write-mapped' || item.action === 'write-neighbor'),
  ) ?? [];
  const gpsStageComplete = gpsPlan !== null && !gpsFailed && (
    gpsExecution?.complete === true || gpsWrites === 0
  );
  const archiveItems = archivePlan?.items.filter(item => item.action === 'archive') ?? [];
  const archiveAttentionItems = archivePlan?.items.filter(item =>
    item.action === 'skip'
    && item.reason !== 'already-archived'
    && item.reason !== 'date-quarantined',
  ) ?? [];
  const archiveItemFailures = archiveExecution?.results.filter(item => item.status === 'failed') ?? [];
  const archiveFailed = archiveExecution?.complete === false || archiveItemFailures.length > 0;
  const archiveSnapshotOnlyFailed = archiveExecution?.complete === false
    && archiveExecution.failed === 0
    && archiveExecution.snapshot_error !== null;
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
  const visiblePhotos: FileInfo[] = scanResult.files.slice(0, visiblePhotoCount);
  const remainingPhotoCount = Math.max(0, scanResult.files.length - visiblePhotos.length);

  return (
    <div className="screen">
      <main className="intake-review">
        <header className="intake-review-head">
          <div>
            <div className="result-status"><span>✓</span> {ingestSummary
              ? archiveExecution?.complete
                ? `已安全歸檔 · ${archiveExecution.archived} 筆進入唯讀收藏庫`
                : gpsExecution?.complete
                ? `位置整理完成 · ${gpsWrittenCount} 筆補值都有溯源標記`
                : dateExecution?.complete
                ? quarantineItems.length > 0
                  ? `日期分級完成 · ${quarantineItems.length} 筆留待人工確認`
                  : '日期分級完成 · 原生日期保留，補值才附溯源標記'
                : trashSummary && trashSummary.completed > 0
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

        {!ingestSummary && (
          <section className={`workflow-next${ingestError ? ' error' : ''}`} aria-label="下一步">
            <span className="workflow-next-number">2</span>
            <div>
              <small>你現在在哪裡：掃描已完成 → 下一步</small>
              <strong>{ingestError ? '尚未建立工作副本' : ingestTitle}</strong>
              <p>{ingestError ?? ingestDescription}</p>
            </div>
            <button className="btn-execute" onClick={onIngest} disabled={ingesting || scanResult.total_files === 0}>
              {ingesting ? '正在驗證與複製…' : `${ingestTitle} →`}
            </button>
          </section>
        )}

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

        <section className="photo-proof" aria-labelledby="photo-proof-title">
          <div className="section-title-row">
            <div>
              <h2 id="photo-proof-title">這就是 phoxif 實際讀到的照片</h2>
              <p>目前僅顯示縮圖；原始檔仍在原資料夾，未移動、未改名、未寫入 metadata。</p>
            </div>
            <span className="read-only-badge">已讀到 {scanResult.files.length.toLocaleString()} 個媒體檔</span>
          </div>
          {scanResult.files.length > 0 ? (
            <>
              <div className="photo-proof-grid">
                {visiblePhotos.map(file => (
                  <PhotoProofCard file={file} formatSize={formatSize} key={file.path} />
                ))}
              </div>
              {remainingPhotoCount > 0 && (
                <button className="btn-gallery-toggle" type="button" onClick={() => setVisiblePhotoCount(count => count + 24)}>
                  再載入 {Math.min(24, remainingPhotoCount)} 個媒體檔（還有 {remainingPhotoCount.toLocaleString()} 個）
                </button>
              )}
            </>
          ) : (
            <div className="photo-proof-empty" role="status">這些路徑可讀取，但沒有找到可支援的照片或影片。</div>
          )}
        </section>

        <section className="decision-section" aria-labelledby="decision-title">
          <div className="section-title-row">
            <div>
              <h2 id="decision-title">phoxif 會怎麼處理</h2>
              <p>{archiveExecution?.complete
                ? '已逐檔複製、讀回驗證 SHA-256 並設為唯讀；來源與工作副本仍保留，Immich 將由 external library 掃描。'
                : gpsExecution
                ? '位置只採用你確認的資料夾映射或可靠時間附近的原生 GPS；其他照片保持無 GPS；尚未碰 NAS。'
                : dateExecution
                ? '日期已依證據分級；自動補值只寫入安全工作檔並留下溯源，待確認項目保持不動；尚未碰 NAS。'
                : trashSummary && trashSummary.completed > 0
                ? '已依你的明確批准，將確認的重複檔移到系統垃圾桶；未批准的來源檔保持原位。'
                : '目前只建立工作副本與追蹤紀錄；不刪來源、不寫 metadata、不碰 NAS。'}</p>
            </div>
          </div>
          <div className="priority-ladder" aria-label="phoxif 處理優先順序">
            <div className="priority-card primary">
              <span className="priority-number">1</span>
              <span className="decision-copy">
                <strong>照片找得到、保得住</strong>
                <small>先盤點、建立內容身分與安全工作副本；來源檔不刪，漏一張比多留一張嚴重。</small>
              </span>
              <span className="priority-policy">最高</span>
            </div>
            <div className="priority-card">
              <span className="priority-number">2</span>
              <span className="decision-copy">
                <strong>拍攝日期、GPS</strong>
                <small>先保留原生資料，再依證據補值；不確定就保持空白或留待確認。</small>
              </span>
            </div>
            <div className="priority-card">
              <span className="priority-number">3</span>
              <span className="decision-copy">
                <strong>重複照片</strong>
                <small>{duplicateCopies + similarFiles > 0
                  ? '內容身分比對可先在背景完成，但清理決定等日期與位置整理後再做。'
                  : '目前沒有重複候選；之後重逢仍會由 catalog 認出。'}</small>
              </span>
            </div>
            <div className="priority-card">
              <span className="priority-number">4</span>
              <span className="decision-copy">
                <strong>垃圾圖／非照片</strong>
                <small>截圖與文件只分流到獨立區，不自動刪除；誤判時仍找得回來。</small>
              </span>
              <span className="priority-policy">最後</span>
            </div>
          </div>
          <p className="intake-scope-note">
            技術上會先用 SHA-256／感知雜湊確認內容身分，目的是避免漏收與找回較完整的原檔；它不是刪除優先權。任何垃圾桶操作都排在日期與位置之後，且必須另外批准。
          </p>
        </section>

        {reviewQueue.length > 0 && (
          <section className="pair-review" aria-label="需要人工判斷的相似照片">
            <div className="section-title-row">
              <div>
                <h2>無法確定是不是同一張：先保留</h2>
                <p>為了讓每張照片都能進日期整理，請確認要留哪張；不確定就選「兩張都留」，這是預設建議。</p>
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
                >兩張都留（建議）</button>
              </article>
            ))}
          </section>
        )}

        {datePlan && dateTotals && unresolvedPairCount === 0 && (
          <section className="dedupe-result" aria-label="日期證據結果">
            <h2>日期證據已分級</h2>
            <p>原生日期不改；檔名／聊天時間／資料夾線索會寫進工作檔並標記來源，落空或可疑者不猜。</p>
            <div className="dedupe-result-grid">
              <div><strong>{dateTotals.native}</strong><span>原生日期保留</span></div>
              <div><strong>{dateTotals.estimated}</strong><span>可補齊並標記</span></div>
              <div><strong>{dateTotals.quarantine}</strong><span>需要人工確認</span></div>
              <div><strong>{dateTotals.skipped}</strong><span>已完成／不適用</span></div>
            </div>
            {dateExecution && (
              <div className="manual-review-note">
                已處理 {dateExecution.results.reduce((sum, result) => sum + result.completed, 0)} 筆；
                {dateExecution.results.reduce((sum, result) => sum + result.failed, 0)} 筆失敗。
              </div>
            )}
            {dateFailed && (
              <div className="date-failure" role="alert">
                <strong>日期階段尚未完成</strong>
                {dateBatchFailures.map(failure => (
                  <small key={`${failure.batch_id}-${failure.error}`}>{failure.batch_id}: {failure.error}</small>
                ))}
                {dateItemFailures.map(failure => (
                  <small key={`${failure.sha256}-${failure.error ?? 'failed'}`}>
                    {failure.sha256.slice(0, 10)}…: {failure.error ?? '處理失敗'}
                  </small>
                ))}
              </div>
            )}
          </section>
        )}

        {quarantineItems.length > 0 && unresolvedPairCount === 0 && (
          <section className="date-quarantine" aria-label="日期待人工確認">
            <div className="section-title-row">
              <div>
                <h2>這些照片沒有可信日期</h2>
                <p>phoxif 沒有猜，也沒有覆寫。它們會留在人工佇列，歸檔時先跳過。</p>
              </div>
              <span className="read-only-badge">{quarantineItems.length} 筆待確認</span>
            </div>
            <details>
              <summary>查看待確認照片</summary>
              <div className="date-quarantine-list">
                {quarantineItems.map(item => (
                  <div key={`${item.batch_id}-${item.sha256}`}>
                    <strong>{item.name}</strong>
                    <small>{item.reason === 'suspicious-native-date'
                      ? '原生日期可疑，因此未覆寫'
                      : item.reason === 'missing-safe-working-copy'
                        ? '找不到可安全修改的工作副本'
                        : '檔名、鄰近照片、資料夾與已核准 mtime 都沒有可信線索'}</small>
                  </div>
                ))}
              </div>
            </details>
          </section>
        )}

        {gpsPlan && gpsTotals && dateStageComplete && (
          <section className="dedupe-result" aria-label="GPS 證據結果">
            <h2>位置證據已保守分級</h2>
            <p>只採用你確認的資料夾映射，或可靠拍攝時間附近的原生 GPS。聊天照片的估計日期絕不拿來推位置。</p>
            <div className="dedupe-result-grid">
              <div><strong>{gpsTotals.native + gpsTotals.previous}</strong><span>已有位置保留</span></div>
              <div><strong>{gpsTotals.mapped}</strong><span>你確認的資料夾</span></div>
              <div><strong>{gpsTotals.neighbor}</strong><span>可靠鄰近位置</span></div>
              <div><strong>{gpsTotals.skipped}</strong><span>保持無 GPS</span></div>
            </div>
            {gpsWriteItems.length > 0 && (
              <details className="gps-plan-details">
                <summary>查看預計補位置的照片</summary>
                <div className="date-quarantine-list">
                  {gpsWriteItems.map(item => (
                    <div key={`${item.batch_id}-${item.sha256}`}>
                      <strong>{item.name}</strong>
                      <small>{item.action === 'write-mapped'
                        ? '使用你在 config 確認的資料夾位置'
                        : `使用原生 GPS 鄰居（時間差最多 ${item.evidence?.offset_seconds ?? 0} 秒）`}</small>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {gpsExecution && (
              <div className="manual-review-note">
                已處理 {gpsExecution.results.reduce((sum, result) => sum + result.completed, 0)} 筆；
                {gpsExecution.results.reduce((sum, result) => sum + result.failed, 0)} 筆失敗。
              </div>
            )}
            {gpsFailed && (
              <div className="date-failure" role="alert">
                <strong>位置階段尚未完成</strong>
                {gpsBatchFailures.map(failure => (
                  <small key={`${failure.batch_id}-${failure.error}`}>{failure.batch_id}: {failure.error}</small>
                ))}
                {gpsItemFailures.map(failure => (
                  <small key={`${failure.sha256}-${failure.error ?? 'failed'}`}>
                    {failure.sha256.slice(0, 10)}…: {failure.error ?? '處理失敗'}
                  </small>
                ))}
              </div>
            )}
          </section>
        )}

        {dedupeSummary && dedupeTotals && gpsStageComplete && (
          <section className="dedupe-result" aria-label="跨來源去重結果">
            <h2>第三優先：重複照片整理</h2>
            <p>日期與位置已先完成。內容身分判斷只寫入 catalog；尚未刪除任何來源或工作副本。</p>
            <div className="dedupe-result-grid">
              <div><strong>{dedupeTotals.exact}</strong><span>同一內容</span></div>
              <div><strong>{dedupeTotals.auto}</strong><span>高信心壓縮版</span></div>
              <div><strong>{dedupeTotals.review}</strong><span>需要你判斷</span></div>
              <div><strong>{dedupeTotals.protected}</strong><span>連拍／編輯版雙留</span></div>
            </div>
          </section>
        )}

        {dedupeSummary && reviewQueue.length === 0 && pendingTrash.length > 0 && gpsStageComplete && (
          <section className="trash-approval" aria-label="待移到系統垃圾桶">
            <div>
              <h2>選用清理：批准重複檔進系統垃圾桶</h2>
              <p>日期與位置已先整理。共 {pendingTrash.length} 筆；不批准也不影響收藏庫。rescue 模式只動工作副本，inbox 模式才會處理收件資料夾中的重複檔。</p>
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

        {trashSummary && pendingTrash.length === 0 && gpsStageComplete && (
          <div className="trash-complete" role="status">
            ✓ 已完成 {trashSummary.completed} 筆垃圾桶操作；{trashSummary.failed} 筆失敗。
          </div>
        )}

        {archivePlan && gpsStageComplete && (
          <section className="archive-review" aria-label="歸檔目的地預覽">
            <div className="section-title-row">
              <div>
                <h2>最後確認：寫入主收藏庫</h2>
                <p>這一步才會寫入目的地。每張先複製到暫存、讀回驗證 SHA-256，再發布為唯讀檔案。</p>
              </div>
              <span className="read-only-badge">需明確批准</span>
            </div>
            <div className="archive-destination">
              <small>收藏庫根目錄</small>
              <strong>{archivePlan.archive_root}</strong>
            </div>
            <div className="dedupe-result-grid">
              <div><strong>{archivePlan.counts.archive}</strong><span>準備歸檔</span></div>
              <div><strong>{formatSize(archivePlan.total_bytes)}</strong><span>寫入容量</span></div>
              <div><strong>{archivePlan.counts['already-archived']}</strong><span>已在收藏庫</span></div>
              <div><strong>{archivePlan.counts.quarantined}</strong><span>日期待確認，跳過</span></div>
            </div>
            {archiveItems.length > 0 && (
              <details className="gps-plan-details" open>
                <summary>逐檔目的地（{archiveItems.length} 筆）</summary>
                <div className="date-quarantine-list archive-path-list">
                  {archiveItems.map(item => (
                    <div key={item.record_id ?? item.sha256}>
                      <strong>{item.name}{item.record_kind === 'sidecar' ? '（編輯 sidecar）' : ''}</strong>
                      <small>→ {item.relative_path}</small>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {archiveAttentionItems.length > 0 && (
              <details className="gps-plan-details" open>
                <summary>尚未歸檔，來源仍保留（{archiveAttentionItems.length} 筆）</summary>
                <div className="date-quarantine-list archive-path-list">
                  {archiveAttentionItems.map(item => (
                    <div key={item.record_id ?? item.sha256}>
                      <strong>{item.name}</strong>
                      <small>{item.reason === 'live-partner-not-ready'
                        ? 'Live Photo 配對尚未完整，整組保留'
                        : item.reason === 'orphan-sidecar'
                          ? 'AAE 找不到同名照片，先保留來源證據'
                          : item.reason === 'sidecar-owner-not-ready'
                            ? 'AAE 所屬照片尚未可歸檔'
                            : item.reason === 'missing-safe-working-copy'
                              ? '安全工作副本遺失或不完整'
                              : item.reason === 'missing-trustworthy-date'
                                ? '沒有可信日期，不能放入時間樹'
                                : '前一步尚未完成，這次不寫入收藏庫'}</small>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {!archiveExecution && archiveItems.length > 0 && (
              <div className="archive-approval">
                <label>
                  <input
                    type="checkbox"
                    checked={archiveConfirmed}
                    onChange={event => setArchiveConfirmed(event.target.checked)}
                  />
                  我已確認收藏庫根目錄、檔案數量與逐檔目的地；批准本次寫入，並同意 catalog 快照只保留最近 8 份（較舊快照會移除）。
                </label>
                <button
                  className="btn-execute"
                  onClick={onArchiveExecute}
                  disabled={!archiveConfirmed || archiving}
                >{archiving ? '正在逐檔驗證與歸檔…' : `批准寫入 ${archiveItems.length} 筆到主收藏庫`}</button>
              </div>
            )}
            {archiveExecution && (
              <div className={archiveFailed ? 'date-failure' : 'archive-complete'} role="status">
                <strong>{archiveFailed
                  ? archiveSnapshotOnlyFailed
                    ? `媒體已全數歸檔（${archiveExecution.archived} 筆），但 catalog 快照失敗`
                    : `部分完成：${archiveExecution.archived} 筆成功、${archiveExecution.failed} 筆失敗`
                  : `完成：${archiveExecution.archived} 筆已歸檔並設為唯讀`}</strong>
                <small>來源與工作副本仍保留；清理必須另行批准。</small>
                {archiveExecution.snapshot_path && <small>Catalog 快照：{archiveExecution.snapshot_path}</small>}
                {archiveExecution.snapshot_error && <small>Catalog 快照失敗：{archiveExecution.snapshot_error}</small>}
                {!archiveFailed && <small>下一步：到 Immich 的 External Libraries 對唯讀收藏庫執行 Scan，再抽查 timeline、搜尋標記與影片播放。</small>}
                {archiveItemFailures.map(item => (
                  <small key={`${item.record_kind}-${item.sha256}`}>{item.sha256.slice(0, 10)}…：{item.error ?? '歸檔失敗'}</small>
                ))}
                {archiveFailed && (
                  <button className="btn-secondary" onClick={onArchiveExecute} disabled={archiving}>
                    {archiving
                      ? '正在安全重試…'
                      : archiveSnapshotOnlyFailed
                        ? '重試 catalog 快照'
                        : '安全重試未完成項目'}
                  </button>
                )}
              </div>
            )}
          </section>
        )}

        {ingestSummary && (
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
                      {ingestSummary.totals.sidecars > 0
                        ? ` 另保留 ${ingestSummary.totals.sidecars.toLocaleString()} 個 AAE 編輯 sidecar。`
                        : ''}
                    </>}
              </span>
              {ingestFailed && (
                <small>{ingestSummary.failures.map((failure) => `${failure.label}: ${failure.error}`).join('；')}</small>
              )}
            </div>
            <button
              className="btn-secondary"
              onClick={ingestFailed
                ? onIngest
                : !dedupeSummary || dedupeFailed
                  ? onDedupe
                  : unresolvedPairCount > 0
                    ? undefined
                    : !datePlan
                      ? onDatePlan
                      : dateFailed
                        ? dateExecution?.complete === false ? onDateExecute : onDatePlan
                      : !dateStageComplete
                        ? onDateExecute
                        : !gpsPlan
                          ? onGpsPlan
                          : gpsFailed
                            ? gpsExecution?.complete === false ? onGpsExecute : onGpsPlan
                            : gpsWrites > 0 && !gpsExecution?.complete
                              ? onGpsExecute
                              : !archivePlan
                                ? onArchivePlan
                                : archiveItems.length === 0
                                  ? onReset
                                  : archiveFailed
                                    ? onArchiveExecute
                                : archiveExecution?.complete
                                  ? onReset
                                  : undefined}
              disabled={ingesting || deduping || dating || gpsing || archiving || (dedupeSummary !== null && unresolvedPairCount > 0) || (archivePlan !== null && archiveItems.length > 0 && archiveExecution?.complete !== true && !archiveFailed)}
            >
              {ingestFailed
                ? (ingesting ? '正在安全重試…' : '安全重試')
                : dedupeSummary
                  ? unresolvedPairCount > 0
                    ? `先確認上方 ${unresolvedPairCount} 組；不確定就兩張都留`
                    : dedupeFailed
                      ? '安全重試比對'
                      : dating
                        ? '正在整理日期證據…'
                        : !datePlan
                          ? '檢查聊天照片日期 →'
                          : dateFailed
                            ? '安全重試日期階段'
                          : !dateStageComplete
                            ? `完成日期分級：保留 ${dateTotals?.native ?? 0}、補值 ${dateTotals?.estimated ?? 0}、隔離 ${dateTotals?.quarantine ?? 0}`
                            : gpsing
                              ? '正在檢查位置證據…'
                              : !gpsPlan
                                ? '檢查可安全補的位置 →'
                                : gpsFailed
                                  ? '安全重試位置階段'
                                  : gpsWrites > 0 && !gpsExecution?.complete
                                    ? `套用 ${gpsWrites} 筆有根據的位置`
                                    : !archivePlan
                                      ? '預覽主收藏庫歸檔位置 →'
                                      : archiveItems.length === 0
                                        ? archiveAttentionItems.length > 0
                                          ? `保留 ${archiveAttentionItems.length} 筆待處理，整理下一批`
                                          : '這批沒有可歸檔項目，整理下一批'
                                        : archiveFailed
                                          ? '安全重試未完成項目'
                                      : archiveExecution?.complete
                                        ? '完成，整理下一批'
                                        : '請在上方確認並批准歸檔'
                  : deduping
                    ? '正在跨來源比對…'
                    : '確認每張照片都有安全身分 →'}
            </button>
            {dedupeError && <small>{dedupeError}</small>}
            {dateError && <small>{dateError}</small>}
            {gpsError && <small>{gpsError}</small>}
            {archiveError && <small>{archiveError}</small>}
          </div>
        )}
      </main>
    </div>
  );
}
