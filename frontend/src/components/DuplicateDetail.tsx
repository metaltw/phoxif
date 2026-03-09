import React, { useState, useCallback } from 'react';
import type { DuplicateGroup, ThumbState } from '../types';
import { DuplicateGroupComponent } from './DuplicateGroup';

interface DuplicateDetailProps {
  groups: DuplicateGroup[];
  dupStates: Map<number, ThumbState[]>;
  onDupStatesChange: (states: Map<number, ThumbState[]>) => void;
  onBack: () => void;
  onDone: () => void;
  formatSize: (bytes: number) => string;
}

type BulkMode = 'largest' | 'newest' | 'clear';

export function DuplicateDetail({
  groups,
  dupStates,
  onDupStatesChange,
  onBack,
  onDone,
  formatSize,
}: DuplicateDetailProps): React.JSX.Element {
  const [activeBulk, setActiveBulk] = useState<BulkMode>('largest');

  const totalFiles = groups.reduce((sum, g) => sum + g.files.length, 0);

  // Calculate stats
  let trashCount = 0;
  let trashSize = 0;
  for (const group of groups) {
    const states = dupStates.get(group.id);
    if (!states) continue;
    states.forEach((state, i) => {
      if (state === 'trash') {
        trashCount++;
        trashSize += group.files[i].size;
      }
    });
  }

  const toggleFile = useCallback((groupId: number, fileIndex: number) => {
    const newStates = new Map(dupStates);
    const groupStates = [...(newStates.get(groupId) ?? [])];
    const current = groupStates[fileIndex];
    if (current === 'keep') {
      groupStates[fileIndex] = 'trash';
    } else if (current === 'trash') {
      groupStates[fileIndex] = 'keep';
    } else {
      groupStates[fileIndex] = 'keep';
    }
    newStates.set(groupId, groupStates);
    onDupStatesChange(newStates);
  }, [dupStates, onDupStatesChange]);

  const applyBulk = useCallback((mode: BulkMode) => {
    setActiveBulk(mode);
    const newStates = new Map(dupStates);

    for (const group of groups) {
      const fileStates: ThumbState[] = group.files.map(() => 'neutral' as ThumbState);

      if (mode === 'clear') {
        // All neutral
      } else if (mode === 'largest') {
        // Keep largest, trash rest
        let bestIdx = 0;
        let bestSize = 0;
        group.files.forEach((f, i) => {
          if (f.size > bestSize) { bestSize = f.size; bestIdx = i; }
        });
        fileStates.forEach((_, i) => {
          fileStates[i] = i === bestIdx ? 'keep' : 'trash';
        });
      } else if (mode === 'newest') {
        // Keep newest by date, trash rest
        let bestIdx = 0;
        let bestDate = '';
        group.files.forEach((f, i) => {
          if (f.date && f.date > bestDate) { bestDate = f.date; bestIdx = i; }
        });
        fileStates.forEach((_, i) => {
          fileStates[i] = i === bestIdx ? 'keep' : 'trash';
        });
      }

      newStates.set(group.id, fileStates);
    }
    onDupStatesChange(newStates);
  }, [dupStates, groups, onDupStatesChange]);

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Duplicate Files</h2>
          <span className="d-meta">{totalFiles} duplicates in {groups.length} groups</span>
        </div>
        <div className="d-sub">
          Click a photo to toggle Keep / Trash.{' '}
          <span className="safe">Trashed files go to system Trash &mdash; always recoverable.</span>
        </div>

        <div className="bulk-bar">
          <span className="bl">Quick:</span>
          <button
            className={`bbtn${activeBulk === 'largest' ? ' on' : ''}`}
            onClick={() => applyBulk('largest')}
          >
            Keep largest
          </button>
          <button
            className={`bbtn${activeBulk === 'newest' ? ' on' : ''}`}
            onClick={() => applyBulk('newest')}
          >
            Keep newest
          </button>
          <button
            className={`bbtn${activeBulk === 'clear' ? ' on' : ''}`}
            onClick={() => applyBulk('clear')}
          >
            Clear all
          </button>
          <div className="bulk-spacer" />
          <span className="bulk-stat">
            {trashCount > 0
              ? `${trashCount} files \u2192 Trash \u00B7 ${formatSize(trashSize)}`
              : 'No files selected for Trash'}
          </span>
        </div>

        {groups.map((group, i) => (
          <DuplicateGroupComponent
            key={group.id}
            group={group}
            groupIndex={i}
            states={dupStates.get(group.id) ?? group.files.map(() => 'neutral' as ThumbState)}
            onToggle={(fileIdx) => toggleFile(group.id, fileIdx)}
            formatSize={formatSize}
          />
        ))}

        <div className="d-done-bar">
          <div className="ddb-info">
            Review complete? <strong>{trashCount} files</strong> will be moved to Trash.
          </div>
          <button className="btn-mark-done" onClick={onDone}>
            {'\u2713'} Done reviewing
          </button>
        </div>
      </div>
    </div>
  );
}
