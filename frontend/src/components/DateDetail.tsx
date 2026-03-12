import React from 'react';
import type { DateMismatch } from '../types';

interface DateDetailProps {
  mismatches: DateMismatch[];
  selectedPaths: Set<string>;
  onSelectionChange: (paths: Set<string>) => void;
  onBack: () => void;
  onDone: () => void;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return iso;
  }
}

export function DateDetail({
  mismatches,
  selectedPaths,
  onSelectionChange,
  onBack,
  onDone,
}: DateDetailProps): React.JSX.Element {
  const allSelected = mismatches.length > 0 && mismatches.every(m => selectedPaths.has(m.file.path));

  const toggleAll = () => {
    if (allSelected) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(mismatches.map(m => m.file.path)));
    }
  };

  const toggle = (path: string) => {
    const next = new Set(selectedPaths);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    onSelectionChange(next);
  };

  return (
    <div className="screen">
      <div className="detail-wrap">
        <div className="detail-top">
          <button className="btn-ghost" onClick={onBack}>{'\u2190'} Back</button>
          <h2>File Dates</h2>
          <span className="detail-count">{mismatches.length} mismatches</span>
        </div>

        <div className="detail-actions" style={{ marginBottom: '1rem', display: 'flex', gap: '0.5rem' }}>
          <button className="btn-ghost" onClick={toggleAll}>
            {allSelected ? 'Clear all' : 'Select all'}
          </button>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            {selectedPaths.size} selected
          </span>
        </div>

        <div className="date-table" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                <th style={{ padding: '0.5rem', width: '2rem' }}></th>
                <th style={{ padding: '0.5rem' }}>Filename</th>
                <th style={{ padding: '0.5rem' }}>Current mtime</th>
                <th style={{ padding: '0.5rem' }}>Target date</th>
                <th style={{ padding: '0.5rem' }}>Source</th>
              </tr>
            </thead>
            <tbody>
              {mismatches.map(m => (
                <tr
                  key={m.file.path}
                  style={{
                    borderBottom: '1px solid var(--border)',
                    opacity: selectedPaths.has(m.file.path) ? 1 : 0.5,
                    cursor: 'pointer',
                  }}
                  onClick={() => toggle(m.file.path)}
                >
                  <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                    <input
                      type="checkbox"
                      checked={selectedPaths.has(m.file.path)}
                      onChange={() => toggle(m.file.path)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                    {m.file.name}
                  </td>
                  <td style={{ padding: '0.5rem', color: 'var(--danger, #e74c3c)' }}>
                    {formatDate(m.file_mtime)}
                  </td>
                  <td style={{ padding: '0.5rem', color: 'var(--success, #27ae60)' }}>
                    {formatDate(m.exif_date)}
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '0.1rem 0.4rem',
                      borderRadius: '4px',
                      background: m.source === 'exif' ? 'var(--tag-bg, #e8f4fd)' : 'var(--tag-bg-alt, #fef3e2)',
                      color: m.source === 'exif' ? 'var(--tag-text, #2980b9)' : 'var(--tag-text-alt, #e67e22)',
                    }}>
                      {m.source}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="detail-bottom" style={{ marginTop: '1.5rem' }}>
          <button className="btn-primary" onClick={onDone}>
            Done reviewing ({selectedPaths.size} selected)
          </button>
        </div>
      </div>
    </div>
  );
}
