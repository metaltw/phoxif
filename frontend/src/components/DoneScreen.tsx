import React, { useState } from 'react';
import { undoSession } from '../api';

interface DoneOperation {
  key: string;
  icon: string;
  label: string;
  detail: string;
  files: string[];
}

interface DoneScreenProps {
  operations: DoneOperation[];
  confirmToggles: Record<string, boolean>;
  onHistory: () => void;
  onNewScan: () => void;
}

export function DoneScreen({
  operations,
  confirmToggles,
  onHistory,
  onNewScan,
}: DoneScreenProps): React.JSX.Element {
  const [undoState, setUndoState] = useState<'idle' | 'loading' | 'done' | 'error'>('idle');
  const [undoError, setUndoError] = useState('');

  const handleUndo = async (): Promise<void> => {
    setUndoState('loading');
    try {
      await undoSession(0);
      setUndoState('done');
    } catch (err) {
      setUndoState('error');
      setUndoError(err instanceof Error ? err.message : 'Undo failed');
    }
  };

  return (
    <div className="screen-center">
      <div className="done-wrap">
        <div className="done-big">{'\u2705'}</div>
        <h2>All Done</h2>
        <div className="done-box">
          {operations.map(op => {
            const wasExecuted = confirmToggles[op.key] !== false;
            return (
              <div className="done-row" key={op.key}>
                <span className="dr-icon">{op.icon}</span>
                <span className="dr-text">{op.label}</span>
                <span className={`dr-stat${wasExecuted ? '' : ' skip'}`}>
                  {wasExecuted ? op.detail : 'Skipped'}
                </span>
              </div>
            );
          })}
          {operations.length === 0 && (
            <div className="done-row">
              <span className="dr-icon">{'\u2713'}</span>
              <span className="dr-text">No operations were executed</span>
              <span className="dr-stat skip">--</span>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', marginTop: '4px' }}>
          <button className="btn-undo" onClick={onHistory}>
            {'\uD83D\uDCCB'} Operation History
          </button>
          <button
            className="btn-undo"
            onClick={() => { void handleUndo(); }}
            disabled={undoState !== 'idle'}
            style={undoState !== 'idle' ? { opacity: 0.5, cursor: 'default' } : undefined}
          >
            {undoState === 'idle' && '\u21A9 Undo this session'}
            {undoState === 'loading' && 'Undoing...'}
            {undoState === 'done' && '\u2713 Undone'}
            {undoState === 'error' && '\u2717 Failed'}
          </button>
        </div>
        {undoState === 'done' && (
          <div style={{ fontSize: '12px', color: 'var(--green)', marginTop: '8px' }}>
            Session undone. Trashed files need manual recovery from system Trash.
          </div>
        )}
        {undoState === 'error' && (
          <div style={{ fontSize: '12px', color: 'var(--red)', marginTop: '8px' }}>
            {undoError}
          </div>
        )}
        <div className="done-note">
          Trashed files {'\u2192'} Finder {'\u2192'} Trash to recover.<br />
          Video originals kept in same folder.<br />
          Log saved to <code>.phoxif_log.json</code>
        </div>
        <button className="btn-newrun" onClick={onNewScan}>
          Start new scan
        </button>
      </div>
    </div>
  );
}
