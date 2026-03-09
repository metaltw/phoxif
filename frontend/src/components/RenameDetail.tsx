import React, { useState, useCallback } from 'react';
import type { RenamePreview } from '../types';

interface RenameDetailProps {
  previews: RenamePreview[];
  selectedPaths: Set<string>;
  onSelectionChange: (selected: Set<string>) => void;
  onBack: () => void;
  onDone: () => void;
}

export function RenameDetail({
  previews,
  selectedPaths,
  onSelectionChange,
  onBack,
  onDone,
}: RenameDetailProps): React.JSX.Element {
  const [selectAll, setSelectAll] = useState(true);

  const toggleFile = useCallback((path: string) => {
    const next = new Set(selectedPaths);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    onSelectionChange(next);
  }, [selectedPaths, onSelectionChange]);

  const handleSelectAll = useCallback((on: boolean) => {
    setSelectAll(on);
    if (on) {
      onSelectionChange(new Set(previews.map(p => p.file.path)));
    } else {
      onSelectionChange(new Set());
    }
  }, [previews, onSelectionChange]);

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Rename by Date</h2>
          <span className="d-meta">{previews.length} files can be renamed</span>
        </div>
        <div className="d-sub">
          Rename files to match their EXIF date: <code>YYYYMMDD_HHMMSS.ext</code>.{' '}
          <span className="safe">This is fully reversible via undo.</span>
        </div>

        <div className="bulk-bar">
          <span className="bl">Selection:</span>
          <button
            className={`bbtn${selectAll ? ' on' : ''}`}
            onClick={() => handleSelectAll(true)}
          >
            Select all
          </button>
          <button
            className={`bbtn${!selectAll ? ' on' : ''}`}
            onClick={() => handleSelectAll(false)}
          >
            Clear all
          </button>
          <div className="bulk-spacer" />
          <span className="bulk-stat">
            {selectedPaths.size} of {previews.length} selected
          </span>
        </div>

        <div className="rename-list">
          {previews.map(preview => {
            const isSelected = selectedPaths.has(preview.file.path);
            return (
              <div
                key={preview.file.path}
                className={`rename-row${isSelected ? ' selected' : ''}`}
                onClick={() => toggleFile(preview.file.path)}
              >
                <div className="rr-check">
                  {isSelected ? '\u2713' : ''}
                </div>
                <div className="rr-old">{preview.old_name}</div>
                <div className="rr-arrow">{'\u2192'}</div>
                <div className="rr-new">{preview.new_name}</div>
                <div className="rr-ext">{preview.file.extension.replace('.', '').toUpperCase()}</div>
              </div>
            );
          })}
        </div>

        <div className="d-done-bar">
          <div className="ddb-info">
            <strong>{selectedPaths.size} files</strong> will be renamed.
          </div>
          <button className="btn-mark-done" onClick={onDone}>
            {'\u2713'} Done reviewing
          </button>
        </div>
      </div>
    </div>
  );
}
