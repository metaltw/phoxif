import React, { useState, useCallback } from 'react';
import type { OrientationIssue } from '../types';
import { Thumbnail, hashColor } from './Thumbnail';

interface OrientationDetailProps {
  issues: OrientationIssue[];
  selectedPaths: Set<string>;
  onSelectionChange: (selected: Set<string>) => void;
  onBack: () => void;
  onDone: () => void;
  formatSize: (bytes: number) => string;
}

export function OrientationDetail({
  issues,
  selectedPaths,
  onSelectionChange,
  onBack,
  onDone,
  formatSize,
}: OrientationDetailProps): React.JSX.Element {
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
      onSelectionChange(new Set(issues.map(i => i.file.path)));
    } else {
      onSelectionChange(new Set());
    }
  }, [issues, onSelectionChange]);

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Orientation Fix</h2>
          <span className="d-meta">{issues.length} photos need rotation correction</span>
        </div>
        <div className="d-sub">
          These photos have EXIF orientation tags that indicate they need rotation.{' '}
          Fixing sets orientation to Normal (1) so all apps display them correctly.{' '}
          <span className="safe">Only the EXIF tag is modified, not the pixel data.</span>
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
            {selectedPaths.size} of {issues.length} selected
          </span>
        </div>

        <div className="orient-grid">
          {issues.map(issue => {
            const isSelected = selectedPaths.has(issue.file.path);
            const gradientColor = hashColor(issue.file.name);
            return (
              <div
                key={issue.file.path}
                className={`orient-item${isSelected ? ' selected' : ''}`}
                onClick={() => toggleFile(issue.file.path)}
              >
                <Thumbnail
                  file={issue.file}
                  state={isSelected ? 'keep' : 'neutral'}
                  onClick={() => toggleFile(issue.file.path)}
                  formatSize={formatSize}
                  gradientColor={gradientColor}
                />
                <div className="orient-label">
                  {issue.label}
                </div>
              </div>
            );
          })}
        </div>

        <div className="d-done-bar">
          <div className="ddb-info">
            <strong>{selectedPaths.size} photos</strong> will have orientation set to Normal.
          </div>
          <button className="btn-mark-done" onClick={onDone}>
            {'\u2713'} Done reviewing
          </button>
        </div>
      </div>
    </div>
  );
}
