import React, { useState, useEffect, useCallback } from 'react';
import type { Session } from '../types';
import { getHistory, undoSession as apiUndoSession } from '../api';

interface HistoryScreenProps {
  onBack: () => void;
}

const ICON_MAP: Record<string, string> = {
  trash: '\uD83D\uDDD1',
  TRASH: '\uD83D\uDDD1',
  rename: '\u270F\uFE0F',
  RENAME: '\u270F\uFE0F',
  gps: '\uD83D\uDCCD',
  GPS: '\uD83D\uDCCD',
  convert: '\uD83C\uDFAC',
  CONVERT: '\uD83C\uDFAC',
  orientation: '\uD83D\uDD04',
  ORIENTATION: '\uD83D\uDD04',
};

export function HistoryScreen({ onBack }: HistoryScreenProps): React.JSX.Element {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [expandedIndices, setExpandedIndices] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [undoingIndex, setUndoingIndex] = useState<number | null>(null);

  useEffect(() => {
    getHistory()
      .then(data => {
        setSessions(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load history');
        setLoading(false);
      });
  }, []);

  const toggleExpand = useCallback((index: number) => {
    setExpandedIndices(prev => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);

  const handleUndo = useCallback(async (index: number) => {
    setUndoingIndex(index);
    try {
      await apiUndoSession(index);
      setSessions(prev => prev.map((s, i) =>
        i === index ? { ...s, undone: true } : s
      ));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Undo failed');
    } finally {
      setUndoingIndex(null);
    }
  }, []);

  if (loading) {
    return (
      <div className="screen-center">
        <div style={{ color: 'var(--dim)', fontSize: '14px' }}>Loading history...</div>
      </div>
    );
  }

  return (
    <div className="screen">
      <div className="hist-wrap" style={{ margin: '0 auto' }}>
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back
        </button>
        <h2>Operation History</h2>
        <div className="hist-sub">
          Every session is logged. Click to expand details, or undo an entire session.
        </div>

        {error && (
          <div style={{
            fontSize: '13px', color: 'var(--red)', marginBottom: '16px',
            padding: '10px 16px', background: 'rgba(239,83,80,0.1)',
            border: '1px solid rgba(239,83,80,0.2)', borderRadius: '8px',
          }}>
            {error}
          </div>
        )}

        {sessions.length === 0 && !error && (
          <div style={{ color: 'var(--dim)', fontSize: '14px', textAlign: 'center', marginTop: '32px' }}>
            No operations yet. History will appear after your first scan & execute.
          </div>
        )}

        {sessions.map((session, i) => {
          const isExpanded = expandedIndices.has(i);
          return (
            <div
              className={`hist-session${session.undone ? ' undone' : ''}`}
              key={i}
            >
              <div className="hs-head" onClick={() => toggleExpand(i)}>
                <span className="hs-date">{'\uD83D\uDCC5'} {session.timestamp}</span>
                <span className="hs-summary">
                  {session.operations.length} operation{session.operations.length !== 1 ? 's' : ''}
                </span>
                {session.undone && <span className="hs-undone-label">UNDONE</span>}
                <div className="hs-spacer" />
                {session.undone ? (
                  <button className="hs-undo undone-btn">{'\u21A9'} Undone</button>
                ) : (
                  <button
                    className="hs-undo"
                    disabled={undoingIndex !== null}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleUndo(i);
                    }}
                  >
                    {undoingIndex === i ? 'Undoing...' : '\u21A9 Undo'}
                  </button>
                )}
              </div>
              {isExpanded && (
                <div className="hs-body" style={{ display: 'block' }}>
                  {session.operations.map((op, j) => (
                    <div className="hs-item" key={j}>
                      <span className="hi-icon">{ICON_MAP[op.type] ?? '\u2022'}</span>
                      <span
                        className="hi-text"
                        style={session.undone ? { textDecoration: 'line-through', color: 'var(--dim)' } : undefined}
                      >
                        {op.file}{op.new_value ? ` \u2192 ${op.new_value}` : ''}
                      </span>
                      <span
                        className="hi-detail"
                        style={session.undone ? { color: 'var(--amber)' } : undefined}
                      >
                        {session.undone ? (op.detail || 'reverted') : op.detail}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
