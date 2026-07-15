import React, { useState, useCallback } from 'react';
import type { Screen, ScanResult, ThumbState, OrientationIssue, IntakeIngestSummary, DateExecutionSummary, DatePlanSummary, DedupePair, DedupeSummary, PendingTrashItem, TrashExecutionSummary } from './types';
import { analyzeDuplicates, approvePendingTrash, executeDates, fetchPendingTrash, ingestSources, planDates, resolveDuplicate } from './api';
import { StepBar } from './components/StepBar';
import { ScanScreen } from './components/ScanScreen';
import { ReviewScreen } from './components/ReviewScreen';
import { DuplicateDetail } from './components/DuplicateDetail';
import { SimilarDetail } from './components/SimilarDetail';
import { OrientationDetail } from './components/OrientationDetail';
import { RenameDetail } from './components/RenameDetail';
import { DateDetail } from './components/DateDetail';
import { NonPhotosDetail } from './components/NonPhotosDetail';
import { ConfirmScreen } from './components/ConfirmScreen';
import { ExecuteScreen } from './components/ExecuteScreen';
import { DoneScreen } from './components/DoneScreen';
import { HistoryScreen } from './components/HistoryScreen';

function formatSize(bytes: number): string {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + ' MB';
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + ' KB';
  return bytes + ' B';
}

const stepMap: Record<Screen, number> = {
  scan: 1, review: 2, duplicates: 2, similar: 2, orientation: 2, rename: 2, dates: 2, 'non-photos': 2,
  confirm: 3, execute: 4, done: 5, history: 5,
};

interface ExecuteOperation {
  key: string;
  icon: string;
  label: string;
  detail: string;
  files: string[];
  action: 'trash' | 'orientation' | 'auto-rotate' | 'rename' | 'fix-dates' | 'move-non-photos';
  actionData?: unknown;
}

export function App(): React.JSX.Element {
  const [screen, setScreen] = useState<Screen>('scan');
  const [prevScreen, setPrevScreen] = useState<Screen>('scan');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [ingesting, setIngesting] = useState(false);
  const [ingestSummary, setIngestSummary] = useState<IntakeIngestSummary | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [dedupeSummary, setDedupeSummary] = useState<DedupeSummary | null>(null);
  const [deduping, setDeduping] = useState(false);
  const [dedupeError, setDedupeError] = useState<string | null>(null);
  const [resolvingPair, setResolvingPair] = useState<string | null>(null);
  const [pendingTrash, setPendingTrash] = useState<PendingTrashItem[]>([]);
  const [trashing, setTrashing] = useState(false);
  const [trashSummary, setTrashSummary] = useState<TrashExecutionSummary | null>(null);
  const [datePlan, setDatePlan] = useState<DatePlanSummary | null>(null);
  const [dateExecution, setDateExecution] = useState<DateExecutionSummary | null>(null);
  const [dating, setDating] = useState(false);
  const [dateError, setDateError] = useState<string | null>(null);
  const [reviewedCategories, setReviewedCategories] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  // Duplicate review state
  const [dupStates, setDupStates] = useState<Map<number, ThumbState[]>>(new Map());
  // Similar photo review state
  const [simStates, setSimStates] = useState<Map<number, ThumbState[]>>(new Map());
  // AI orientation results
  const [aiOrientIssues, setAiOrientIssues] = useState<OrientationIssue[]>([]);
  // Orientation selected paths
  const [orientSelected, setOrientSelected] = useState<Set<string>>(new Set());
  // Rename selected paths
  const [renameSelected, setRenameSelected] = useState<Set<string>>(new Set());
  // Date fix selected paths
  const [dateSelected, setDateSelected] = useState<Set<string>>(new Set());
  // Non-photos selected paths
  const [nonPhotoSelected, setNonPhotoSelected] = useState<Set<string>>(new Set());
  // Skipped categories (reviewed but user wants to bypass)
  const [skippedCategories, setSkippedCategories] = useState<Set<string>>(new Set());
  // Confirm toggles and operation order
  const [confirmToggles, setConfirmToggles] = useState<Record<string, boolean>>({});
  const [operationOrder, setOperationOrder] = useState<string[]>([]);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 2000);
  }, []);

  const navigateTo = useCallback((s: Screen) => {
    setScreen(prev => {
      setPrevScreen(prev);
      return s;
    });
  }, []);

  const handleScanComplete = useCallback((result: ScanResult) => {
    setScanResult(result);
    setIngestSummary(null);
    setIngestError(null);
    setDedupeSummary(null);
    setDedupeError(null);
    setPendingTrash([]);
    setTrashSummary(null);
    setDatePlan(null);
    setDateExecution(null);
    setDateError(null);

    // Init duplicate states
    const dStates = new Map<number, ThumbState[]>();
    for (const group of result.duplicates) {
      dStates.set(group.id, group.files.map((_, i) =>
        i === group.keep_index ? 'keep' : 'trash'
      ));
    }
    setDupStates(dStates);

    // Init similar states
    const sStates = new Map<number, ThumbState[]>();
    for (const group of result.similar_groups) {
      sStates.set(group.id, group.files.map((_, i) =>
        i === group.keep_index ? 'keep' : 'trash'
      ));
    }
    setSimStates(sStates);

    // Reset AI orientation state (will be populated from OrientationDetail)
    setAiOrientIssues([]);
    setOrientSelected(new Set());

    // Init rename selection (all selected by default)
    setRenameSelected(new Set(result.rename_preview.map(r => r.file.path)));

    // Init date fix selection (all selected by default)
    setDateSelected(new Set(result.date_mismatches.map(d => d.file.path)));

    // Init non-photo selection (all selected by default)
    setNonPhotoSelected(new Set(result.non_photos.map(n => n.file.path)));

    setReviewedCategories(new Set());
    setConfirmToggles({});
    navigateTo('review');
  }, [navigateTo]);

  const handleIngest = useCallback(async () => {
    if (!scanResult) return;
    setIngesting(true);
    setIngestError(null);
    try {
      const summary = await ingestSources(scanResult.source_paths, scanResult.mode);
      setIngestSummary(summary);
    } catch (error) {
      setIngestError(error instanceof Error ? error.message : '建立工作副本失敗');
    } finally {
      setIngesting(false);
    }
  }, [scanResult]);

  const handleDedupe = useCallback(async () => {
    if (!ingestSummary) return;
    setDeduping(true);
    setDedupeError(null);
    try {
      const summary = await analyzeDuplicates(ingestSummary.batches.map(batch => batch.batch_id));
      const trashItems = await fetchPendingTrash(ingestSummary.batches.map(batch => batch.batch_id));
      setDedupeSummary(summary);
      setPendingTrash(trashItems);
    } catch (error) {
      setDedupeError(error instanceof Error ? error.message : '重複照片分析失敗');
    } finally {
      setDeduping(false);
    }
  }, [ingestSummary]);

  const handleResolvePair = useCallback(async (
    batchId: string,
    pair: DedupePair,
    keepSha256: string | null,
  ) => {
    if (!ingestSummary || pair.files.length !== 2) return;
    setResolvingPair(pair.id);
    setDedupeError(null);
    let decisionSaved = false;
    try {
      await resolveDuplicate(
        batchId,
        pair.id,
        pair.files[0].sha256,
        pair.files[1].sha256,
        keepSha256,
      );
      decisionSaved = true;
      const batchIds = ingestSummary.batches.map(batch => batch.batch_id);
      const refreshedSummary = await analyzeDuplicates(batchIds);
      setDedupeSummary(refreshedSummary);
      setPendingTrash(await fetchPendingTrash(batchIds));
    } catch (error) {
      if (decisionSaved) {
        setDedupeSummary(null);
        setDedupeError('判斷已儲存，但最新比對結果載入失敗；請重新檢查重複照片。');
      } else {
        setDedupeError(error instanceof Error ? error.message : '無法儲存人工判斷');
      }
    } finally {
      setResolvingPair(null);
    }
  }, [ingestSummary]);

  const handleApproveTrash = useCallback(async () => {
    if (pendingTrash.length === 0) return;
    setTrashing(true);
    setDedupeError(null);
    try {
      const summary = await approvePendingTrash(pendingTrash.map(item => item.operation_id));
      setTrashSummary(summary);
      if (ingestSummary) {
        setPendingTrash(await fetchPendingTrash(ingestSummary.batches.map(batch => batch.batch_id)));
      }
    } catch (error) {
      setDedupeError(error instanceof Error ? error.message : '移到系統垃圾桶失敗');
    } finally {
      setTrashing(false);
    }
  }, [ingestSummary, pendingTrash]);

  const handleDatePlan = useCallback(async () => {
    if (!ingestSummary) return;
    setDating(true);
    setDateError(null);
    try {
      const result = await planDates(ingestSummary.batches.map(batch => batch.batch_id));
      setDatePlan(result);
      if (!result.complete) {
        setDateError('部分來源的日期證據無法讀取；請查看錯誤後安全重試。');
      }
    } catch (error) {
      setDateError(error instanceof Error ? error.message : '日期證據分析失敗');
    } finally {
      setDating(false);
    }
  }, [ingestSummary]);

  const handleDateExecute = useCallback(async () => {
    if (!ingestSummary) return;
    setDating(true);
    setDateError(null);
    try {
      const result = await executeDates(ingestSummary.batches.map(batch => batch.batch_id));
      setDateExecution(result);
      if (!result.complete) {
        setDateError('部分日期處理失敗；未成功的檔案不會被當成已完成。');
        return;
      }
      const refreshed = await planDates(ingestSummary.batches.map(batch => batch.batch_id));
      setDatePlan(refreshed);
      if (!refreshed.complete) {
        setDateError('日期已處理，但最新狀態載入不完整；請安全重試檢查。');
      }
    } catch (error) {
      setDateError(error instanceof Error ? error.message : '日期補齊失敗');
    } finally {
      setDating(false);
    }
  }, [ingestSummary]);

  const resetIntake = useCallback(() => {
    setScanResult(null);
    setIngestSummary(null);
    setIngestError(null);
    setDedupeSummary(null);
    setDedupeError(null);
    setResolvingPair(null);
    setPendingTrash([]);
    setTrashSummary(null);
    setDatePlan(null);
    setDateExecution(null);
    setDateError(null);
    navigateTo('scan');
  }, [navigateTo]);

  const markReviewed = useCallback((category: string) => {
    setReviewedCategories(prev => {
      const next = new Set(prev);
      next.add(category);
      return next;
    });
    showToast(`${category} reviewed`);
  }, [showToast]);

  // Build operations list for confirm/execute
  const buildOperations = useCallback((): ExecuteOperation[] => {
    if (!scanResult) return [];
    const ops: ExecuteOperation[] = [];

    // Duplicate trash
    if (reviewedCategories.has('duplicates') && !skippedCategories.has('duplicates')) {
      const trashPaths: string[] = [];
      let trashSize = 0;
      for (const group of scanResult.duplicates) {
        const states = dupStates.get(group.id);
        if (!states) continue;
        states.forEach((state, i) => {
          if (state === 'trash') {
            trashPaths.push(group.files[i].path);
            trashSize += group.files[i].size;
          }
        });
      }
      if (trashPaths.length > 0) {
        ops.push({
          key: 'dup-trash',
          icon: '\uD83D\uDCCB',
          label: `Trash ${trashPaths.length} duplicate files`,
          detail: `Saving ${formatSize(trashSize)}`,
          files: trashPaths,
          action: 'trash',
        });
      }
    }

    // Similar trash
    if (reviewedCategories.has('similar') && !skippedCategories.has('similar')) {
      const trashPaths: string[] = [];
      let trashSize = 0;
      for (const group of scanResult.similar_groups) {
        const states = simStates.get(group.id);
        if (!states) continue;
        states.forEach((state, i) => {
          if (state === 'trash') {
            trashPaths.push(group.files[i].path);
            trashSize += group.files[i].size;
          }
        });
      }
      if (trashPaths.length > 0) {
        ops.push({
          key: 'sim-trash',
          icon: '\uD83D\uDD0D',
          label: `Trash ${trashPaths.length} similar photos`,
          detail: `Saving ${formatSize(trashSize)}`,
          files: trashPaths,
          action: 'trash',
        });
      }
    }

    // Auto-rotate (AI orientation)
    if (reviewedCategories.has('orientation') && !skippedCategories.has('orientation') && orientSelected.size > 0) {
      const selectedIssues = aiOrientIssues.filter(
        i => orientSelected.has(i.file.path)
      );
      if (selectedIssues.length > 0) {
        ops.push({
          key: 'orient-fix',
          icon: '\uD83D\uDD04',
          label: `Auto-rotate ${selectedIssues.length} files`,
          detail: 'AI-detected rotation correction',
          files: selectedIssues.map(i => i.file.path),
          action: 'auto-rotate',
          actionData: selectedIssues.map(i => ({
            path: i.file.path,
            rotation: i.rotation,
          })),
        });
      }
    }

    // Move non-photos (before rename to avoid path conflicts)
    if (reviewedCategories.has('non-photos') && !skippedCategories.has('non-photos') && nonPhotoSelected.size > 0) {
      const selectedItems = scanResult.non_photos.filter(
        n => nonPhotoSelected.has(n.file.path)
      );
      if (selectedItems.length > 0) {
        ops.push({
          key: 'move-nonphotos',
          icon: '\uD83D\uDCC2',
          label: `Move ${selectedItems.length} non-photo files`,
          detail: 'To _non_photos/ subfolders',
          files: selectedItems.map(n => n.file.path),
          action: 'move-non-photos',
          actionData: {
            files: selectedItems.map(n => ({
              path: n.file.path,
              category: n.category,
            })),
            baseDir: scanResult.base_dir,
          },
        });
      }
    }

    // Rename (exclude files being moved to _non_photos/)
    if (reviewedCategories.has('rename') && !skippedCategories.has('rename') && renameSelected.size > 0) {
      const selectedRenames = scanResult.rename_preview.filter(
        r => renameSelected.has(r.file.path) && !nonPhotoSelected.has(r.file.path)
      );
      if (selectedRenames.length > 0) {
        ops.push({
          key: 'rename-date',
          icon: '\u270F\uFE0F',
          label: `Rename ${selectedRenames.length} files by date`,
          detail: 'YYYYMMDD_HHMMSS format',
          files: selectedRenames.map(r => r.file.path),
          action: 'rename',
          actionData: selectedRenames.map(r => ({
            old: r.file.path,
            new: r.new_path,
          })),
        });
      }
    }

    // Fix dates (exclude files being moved, always last before sort)
    if (reviewedCategories.has('dates') && !skippedCategories.has('dates') && dateSelected.size > 0) {
      const selectedDates = scanResult.date_mismatches.filter(
        d => dateSelected.has(d.file.path) && !nonPhotoSelected.has(d.file.path)
      );
      if (selectedDates.length > 0) {
        ops.push({
          key: 'fix-dates',
          icon: '\uD83D\uDCC5',
          label: `Fix dates on ${selectedDates.length} files`,
          detail: 'Set mtime to match EXIF/filename date',
          files: selectedDates.map(d => d.file.path),
          action: 'fix-dates',
          actionData: selectedDates.map(d => ({
            path: d.file.path,
            target_date: d.exif_date,
          })),
        });
      }
    }

    // Sort by user-defined order if available
    if (operationOrder.length > 0) {
      ops.sort((a, b) => {
        const ai = operationOrder.indexOf(a.key);
        const bi = operationOrder.indexOf(b.key);
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
      });
    }

    return ops;
  }, [scanResult, reviewedCategories, skippedCategories, dupStates, simStates, orientSelected, renameSelected, dateSelected, nonPhotoSelected, operationOrder, aiOrientIssues]);

  const currentStep = stepMap[screen] ?? 1;

  return (
    <>
      <div className="topbar">
        <div className="logo">phoxif <span>照片收件匣</span></div>
        <StepBar currentStep={currentStep} />
        <div className="topbar-spacer" />
        <button className="btn-ghost" onClick={() => navigateTo('history')}>
          {'\uD83D\uDCCB'} 整理紀錄
        </button>
      </div>

      {screen === 'scan' && (
        <ScanScreen onComplete={handleScanComplete} />
      )}

      {screen === 'review' && scanResult && (
        <ReviewScreen
          scanResult={scanResult}
          ingesting={ingesting}
          ingestSummary={ingestSummary}
          ingestError={ingestError}
          dedupeSummary={dedupeSummary}
          deduping={deduping}
          dedupeError={dedupeError}
          resolvingPair={resolvingPair}
          pendingTrash={pendingTrash}
          trashing={trashing}
          trashSummary={trashSummary}
          datePlan={datePlan}
          dateExecution={dateExecution}
          dating={dating}
          dateError={dateError}
          onIngest={handleIngest}
          onDedupe={handleDedupe}
          onResolvePair={handleResolvePair}
          onApproveTrash={handleApproveTrash}
          onDatePlan={handleDatePlan}
          onDateExecute={handleDateExecute}
          onReset={resetIntake}
          formatSize={formatSize}
        />
      )}

      {screen === 'duplicates' && scanResult && (
        <DuplicateDetail
          groups={scanResult.duplicates}
          dupStates={dupStates}
          onDupStatesChange={setDupStates}
          onBack={() => {
            markReviewed('duplicates');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('duplicates');
            navigateTo('review');
          }}
          formatSize={formatSize}
        />
      )}

      {screen === 'similar' && scanResult && (
        <SimilarDetail
          groups={scanResult.similar_groups}
          simStates={simStates}
          onSimStatesChange={setSimStates}
          onBack={() => {
            markReviewed('similar');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('similar');
            navigateTo('review');
          }}
          formatSize={formatSize}
        />
      )}

      {screen === 'orientation' && scanResult && (
        <OrientationDetail
          scanPath={scanResult.base_dir}
          aiOrientIssues={aiOrientIssues}
          onAiOrientIssuesChange={setAiOrientIssues}
          selectedPaths={orientSelected}
          onSelectionChange={setOrientSelected}
          onBack={() => {
            markReviewed('orientation');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('orientation');
            navigateTo('review');
          }}
        />
      )}

      {screen === 'rename' && scanResult && (
        <RenameDetail
          previews={scanResult.rename_preview}
          selectedPaths={renameSelected}
          onSelectionChange={setRenameSelected}
          onBack={() => {
            markReviewed('rename');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('rename');
            navigateTo('review');
          }}
        />
      )}

      {screen === 'dates' && scanResult && (
        <DateDetail
          mismatches={scanResult.date_mismatches}
          selectedPaths={dateSelected}
          onSelectionChange={setDateSelected}
          onBack={() => {
            markReviewed('dates');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('dates');
            navigateTo('review');
          }}
        />
      )}

      {screen === 'non-photos' && scanResult && (
        <NonPhotosDetail
          nonPhotos={scanResult.non_photos}
          selectedPaths={nonPhotoSelected}
          onSelectionChange={setNonPhotoSelected}
          formatSize={formatSize}
          onBack={() => {
            markReviewed('non-photos');
            navigateTo('review');
          }}
          onDone={() => {
            markReviewed('non-photos');
            navigateTo('review');
          }}
        />
      )}

      {screen === 'confirm' && (
        <ConfirmScreen
          operations={buildOperations()}
          confirmToggles={confirmToggles}
          onToggle={(key) => {
            setConfirmToggles(prev => ({ ...prev, [key]: !(prev[key] ?? true) }));
          }}
          onReorder={setOperationOrder}
          onBack={() => navigateTo('review')}
          onExecute={() => navigateTo('execute')}
        />
      )}

      {screen === 'execute' && (
        <ExecuteScreen
          operations={buildOperations().filter(op => confirmToggles[op.key] !== false)}
          onComplete={() => navigateTo('done')}
        />
      )}

      {screen === 'done' && (
        <DoneScreen
          operations={buildOperations()}
          confirmToggles={confirmToggles}
          onHistory={() => navigateTo('history')}
          onNewScan={() => {
            setScanResult(null);
            setReviewedCategories(new Set());
            setSkippedCategories(new Set());
            setDupStates(new Map());
            setSimStates(new Map());
            setAiOrientIssues([]);
            setOrientSelected(new Set());
            setRenameSelected(new Set());
            setDateSelected(new Set());
            setNonPhotoSelected(new Set());
            setConfirmToggles({});
            setOperationOrder([]);
            navigateTo('scan');
          }}
        />
      )}

      {screen === 'history' && (
        <HistoryScreen onBack={() => navigateTo(prevScreen === 'history' ? 'scan' : prevScreen)} />
      )}

      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
