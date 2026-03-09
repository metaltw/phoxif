import React, { useState, useCallback } from 'react';
import type { SimilarGroup, ThumbState } from '../types';
import { Thumbnail, hashColor } from './Thumbnail';
import { revealInFinder } from '../api';

interface SimilarDetailProps {
  groups: SimilarGroup[];
  simStates: Map<number, ThumbState[]>;
  onSimStatesChange: (states: Map<number, ThumbState[]>) => void;
  onBack: () => void;
  onDone: () => void;
  formatSize: (bytes: number) => string;
}

type BulkMode = 'best' | 'clear';

export function SimilarDetail({
  groups,
  simStates,
  onSimStatesChange,
  onBack,
  onDone,
  formatSize,
}: SimilarDetailProps): React.JSX.Element {
  const [activeBulk, setActiveBulk] = useState<BulkMode>('best');

  const totalFiles = groups.reduce((sum, g) => sum + g.files.length, 0);

  let trashCount = 0;
  let trashSize = 0;
  for (const group of groups) {
    const states = simStates.get(group.id);
    if (!states) continue;
    states.forEach((state, i) => {
      if (state === 'trash') {
        trashCount++;
        trashSize += group.files[i].size;
      }
    });
  }

  const toggleFile = useCallback((groupId: number, fileIndex: number) => {
    const newStates = new Map(simStates);
    const groupStates = [...(newStates.get(groupId) ?? [])];
    const current = groupStates[fileIndex];
    groupStates[fileIndex] = current === 'keep' ? 'trash' : 'keep';
    newStates.set(groupId, groupStates);
    onSimStatesChange(newStates);
  }, [simStates, onSimStatesChange]);

  const applyBulk = useCallback((mode: BulkMode) => {
    setActiveBulk(mode);
    const newStates = new Map(simStates);
    for (const group of groups) {
      const fileStates: ThumbState[] = group.files.map(() => 'neutral' as ThumbState);
      if (mode === 'best') {
        fileStates.forEach((_, i) => {
          fileStates[i] = i === group.keep_index ? 'keep' : 'trash';
        });
      }
      newStates.set(group.id, fileStates);
    }
    onSimStatesChange(newStates);
  }, [simStates, groups, onSimStatesChange]);

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Similar Photos</h2>
          <span className="d-meta">{totalFiles} photos in {groups.length} groups</span>
        </div>
        <div className="d-sub">
          These photos look visually similar (taken in burst or near-identical shots).{' '}
          Click to toggle Keep / Trash.{' '}
          <span className="safe">Trashed files go to system Trash.</span>
        </div>

        <div className="bulk-bar">
          <span className="bl">Quick:</span>
          <button
            className={`bbtn${activeBulk === 'best' ? ' on' : ''}`}
            onClick={() => applyBulk('best')}
          >
            Keep best quality
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

        {groups.map((group, i) => {
          const states = simStates.get(group.id) ?? group.files.map(() => 'neutral' as ThumbState);
          const gradientColor = hashColor(group.files[0].name);
          const avgSimilarity = group.similarities.length > 0
            ? Math.round(group.similarities.reduce((s, e) => s + e.similarity, 0) / group.similarities.length * 100)
            : 0;

          const folderPath = group.files[0].path.substring(0, group.files[0].path.lastIndexOf('/'));

          return (
            <div className="grp" key={group.id}>
              <div className="grp-head">
                <span className="gl">Group {i + 1}</span>
                <span className="gm">
                  {group.files.length} similar &middot; {group.reason === 'burst' ? 'burst shots' : `${avgSimilarity}% match`} &middot; {formatSize(group.reclaimable_size)} reclaimable
                </span>
                <button
                  className="grp-finder"
                  onClick={(e) => { e.stopPropagation(); void revealInFinder(folderPath); }}
                  title="Show in Finder"
                >
                  Finder {'\u2197'}
                </button>
              </div>
              <div className="grp-body">
                {group.files.map((file, fi) => (
                  <div key={file.path} style={{ position: 'relative' }}>
                    <Thumbnail
                      file={file}
                      state={states[fi]}
                      onClick={() => toggleFile(group.id, fi)}
                      formatSize={formatSize}
                      gradientColor={gradientColor}
                    />
                    {file.width && file.height && (
                      <div style={{
                        position: 'absolute', bottom: '42px', left: '4px',
                        fontSize: '10px', color: 'rgba(255,255,255,0.6)',
                        background: 'rgba(0,0,0,0.5)', padding: '1px 5px', borderRadius: '3px',
                      }}>
                        {file.width}&times;{file.height}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}

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
