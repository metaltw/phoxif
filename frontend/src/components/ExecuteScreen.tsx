import React, { useState, useEffect, useRef } from 'react';
import { trashFiles, fixOrientation, executeRenames, autoRotate, fixFileDates, moveNonPhotos } from '../api';

interface ExecuteOperation {
  key: string;
  icon: string;
  label: string;
  detail: string;
  files: string[];
  action: 'trash' | 'orientation' | 'auto-rotate' | 'rename' | 'fix-dates' | 'move-non-photos';
  actionData?: unknown;
}

interface ExecuteScreenProps {
  operations: ExecuteOperation[];
  onComplete: () => void;
}

type ItemStatus = 'waiting' | 'running' | 'done' | 'error';

export function ExecuteScreen({ operations, onComplete }: ExecuteScreenProps): React.JSX.Element {
  const [statuses, setStatuses] = useState<ItemStatus[]>(
    operations.map(() => 'waiting')
  );
  const [errors, setErrors] = useState<string[]>(operations.map(() => ''));
  const [progress, setProgress] = useState(0);
  const [progressText, setProgressText] = useState('Starting...');
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;

    let currentIndex = 0;
    let hasError = false;

    async function executeOp(op: ExecuteOperation): Promise<void> {
      if (op.action === 'trash') {
        await trashFiles(op.files);
      } else if (op.action === 'orientation') {
        const data = op.actionData as Array<{ path: string; orientation: number }>;
        await fixOrientation(data);
      } else if (op.action === 'auto-rotate') {
        const data = op.actionData as Array<{ path: string; rotation: number }>;
        await autoRotate(data);
      } else if (op.action === 'rename') {
        const data = op.actionData as Array<{ old: string; new: string }>;
        await executeRenames(data);
      } else if (op.action === 'fix-dates') {
        const data = op.actionData as Array<{ path: string; target_date: string }>;
        await fixFileDates(data);
      } else if (op.action === 'move-non-photos') {
        const data = op.actionData as { files: Array<{ path: string; category: string }>; baseDir: string };
        await moveNonPhotos(data.files, data.baseDir);
      }
    }

    async function executeNext(): Promise<void> {
      if (currentIndex >= operations.length) {
        setProgress(100);
        setProgressText(hasError ? 'Completed with errors' : 'Complete!');
        setTimeout(onComplete, 800);
        return;
      }

      const i = currentIndex;
      const op = operations[i];

      setStatuses(prev => {
        const next = [...prev];
        next[i] = 'running';
        return next;
      });
      setProgress(((i + 0.5) / operations.length) * 100);
      setProgressText(`${op.label}...`);

      let failed = false;
      try {
        await executeOp(op);
      } catch (err) {
        failed = true;
        hasError = true;
        const msg = err instanceof Error ? err.message : 'Operation failed';
        setErrors(prev => {
          const next = [...prev];
          next[i] = msg;
          return next;
        });
      }

      await new Promise(resolve => setTimeout(resolve, 600));

      setStatuses(prev => {
        const next = [...prev];
        next[i] = failed ? 'error' : 'done';
        return next;
      });
      setProgress(((i + 1) / operations.length) * 100);

      currentIndex++;
      setTimeout(executeNext, 400);
    }

    setTimeout(() => { void executeNext(); }, 500);
  }, [operations, onComplete]);

  return (
    <div className="screen-center">
      <div className="exec-wrap">
        <h2>Executing...</h2>
        <div className="exec-list">
          {operations.map((op, i) => (
            <div className="exec-item" key={op.key}>
              <div className="ei-icon">{op.icon}</div>
              <div className="ei-text">{op.label}</div>
              <div className={`ei-status${statuses[i] === 'done' ? ' done' : ''}${statuses[i] === 'running' ? ' running' : ''}${statuses[i] === 'error' ? ' error' : ''}`}>
                {statuses[i] === 'waiting' && 'Waiting'}
                {statuses[i] === 'running' && 'Running...'}
                {statuses[i] === 'done' && '\u2713 Done'}
                {statuses[i] === 'error' && '\u2717 Failed'}
              </div>
              {errors[i] && (
                <div className="ei-error">{errors[i]}</div>
              )}
            </div>
          ))}
        </div>
        <div className="exec-pbar">
          <div className="exec-pfill" style={{ width: `${progress}%` }} />
        </div>
        <div className="exec-ptext">{progressText}</div>
      </div>
    </div>
  );
}
