import React, { useState, useCallback } from 'react';
import type { Screen, ScanResult, ThumbState } from './types';
import { StepBar } from './components/StepBar';
import { ScanScreen } from './components/ScanScreen';
import { ReviewScreen } from './components/ReviewScreen';
import { DuplicateDetail } from './components/DuplicateDetail';
import { SimilarDetail } from './components/SimilarDetail';
import { OrientationDetail } from './components/OrientationDetail';
import { RenameDetail } from './components/RenameDetail';
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
  scan: 1, review: 2, duplicates: 2, similar: 2, orientation: 2, rename: 2,
  confirm: 3, execute: 4, done: 5, history: 5,
};

interface ExecuteOperation {
  key: string;
  icon: string;
  label: string;
  detail: string;
  files: string[];
  action: 'trash' | 'orientation' | 'rename';
  actionData?: unknown;
}

export function App(): React.JSX.Element {
  const [screen, setScreen] = useState<Screen>('scan');
  const [prevScreen, setPrevScreen] = useState<Screen>('scan');
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [reviewedCategories, setReviewedCategories] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  // Duplicate review state
  const [dupStates, setDupStates] = useState<Map<number, ThumbState[]>>(new Map());
  // Similar photo review state
  const [simStates, setSimStates] = useState<Map<number, ThumbState[]>>(new Map());
  // Orientation selected paths
  const [orientSelected, setOrientSelected] = useState<Set<string>>(new Set());
  // Rename selected paths
  const [renameSelected, setRenameSelected] = useState<Set<string>>(new Set());
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

    // Init orientation selection (all selected by default)
    setOrientSelected(new Set(result.orientation_issues.map(i => i.file.path)));

    // Init rename selection (all selected by default)
    setRenameSelected(new Set(result.rename_preview.map(r => r.file.path)));

    setReviewedCategories(new Set());
    setConfirmToggles({});
    navigateTo('review');
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

    // Orientation fix
    if (reviewedCategories.has('orientation') && !skippedCategories.has('orientation') && orientSelected.size > 0) {
      const selectedIssues = scanResult.orientation_issues.filter(
        i => orientSelected.has(i.file.path)
      );
      if (selectedIssues.length > 0) {
        ops.push({
          key: 'orient-fix',
          icon: '\uD83D\uDD04',
          label: `Fix orientation on ${selectedIssues.length} photos`,
          detail: 'Set EXIF orientation to Normal',
          files: selectedIssues.map(i => i.file.path),
          action: 'orientation',
          actionData: selectedIssues.map(i => ({
            path: i.file.path,
            orientation: i.current_orientation,
          })),
        });
      }
    }

    // Rename
    if (reviewedCategories.has('rename') && !skippedCategories.has('rename') && renameSelected.size > 0) {
      const selectedRenames = scanResult.rename_preview.filter(
        r => renameSelected.has(r.file.path)
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

    // Sort by user-defined order if available
    if (operationOrder.length > 0) {
      ops.sort((a, b) => {
        const ai = operationOrder.indexOf(a.key);
        const bi = operationOrder.indexOf(b.key);
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
      });
    }

    return ops;
  }, [scanResult, reviewedCategories, skippedCategories, dupStates, simStates, orientSelected, renameSelected, operationOrder]);

  const toggleSkip = useCallback((category: string) => {
    setSkippedCategories(prev => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  }, []);

  const currentStep = stepMap[screen] ?? 1;

  return (
    <>
      <div className="topbar">
        <div className="logo">phoxif <span>v0.1</span></div>
        <StepBar currentStep={currentStep} />
        <div className="topbar-spacer" />
        <button className="btn-ghost" onClick={() => navigateTo('history')}>
          {'\uD83D\uDCCB'} History
        </button>
      </div>

      {screen === 'scan' && (
        <ScanScreen onComplete={handleScanComplete} />
      )}

      {screen === 'review' && scanResult && (
        <ReviewScreen
          scanResult={scanResult}
          reviewedCategories={reviewedCategories}
          skippedCategories={skippedCategories}
          onToggleSkip={toggleSkip}
          dupStates={dupStates}
          simStates={simStates}
          orientSelected={orientSelected}
          renameSelected={renameSelected}
          onNavigate={navigateTo}
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
          issues={scanResult.orientation_issues}
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
          formatSize={formatSize}
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
            setOrientSelected(new Set());
            setRenameSelected(new Set());
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
