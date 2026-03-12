import React, { useState, useCallback, useEffect } from 'react';
import type { OrientationIssue } from '../types';
import { detectOrientation } from '../api';

interface OrientationDetailProps {
  scanPath: string;
  aiOrientIssues: OrientationIssue[];
  onAiOrientIssuesChange: (issues: OrientationIssue[]) => void;
  selectedPaths: Set<string>;
  onSelectionChange: (selected: Set<string>) => void;
  onBack: () => void;
  onDone: () => void;
}

type ScanPhase = 'idle' | 'scanning' | 'done' | 'error';

export function OrientationDetail({
  scanPath,
  aiOrientIssues,
  onAiOrientIssuesChange,
  selectedPaths,
  onSelectionChange,
  onBack,
  onDone,
}: OrientationDetailProps): React.JSX.Element {
  const [phase, setPhase] = useState<ScanPhase>(aiOrientIssues.length > 0 ? 'done' : 'idle');
  const [errorMsg, setErrorMsg] = useState('');
  const [scannedCount, setScannedCount] = useState(0);
  const [progressCurrent, setProgressCurrent] = useState(0);
  const [progressTotal, setProgressTotal] = useState(0);
  const [progressFilename, setProgressFilename] = useState('');

  const hasScanned = phase === 'done';
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  // Close lightbox on Esc
  useEffect(() => {
    if (lightboxIndex === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightboxIndex(null);
      if (e.key === 'ArrowLeft' && lightboxIndex > 0) setLightboxIndex(lightboxIndex - 1);
      if (e.key === 'ArrowRight' && lightboxIndex < aiOrientIssues.length - 1) setLightboxIndex(lightboxIndex + 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxIndex, aiOrientIssues.length]);

  const handleDetect = useCallback(async () => {
    setPhase('scanning');
    setErrorMsg('');
    try {
      const result = await detectOrientation(scanPath, (p) => {
        setProgressCurrent(p.current);
        setProgressTotal(p.total);
        setProgressFilename(p.filename);
      });
      const issues = result.issues as OrientationIssue[];
      onAiOrientIssuesChange(issues);
      setScannedCount(result.scanned_count);

      // Auto-select files with confidence >= 0.7
      const autoSelected = new Set<string>();
      for (const issue of issues) {
        if (issue.confidence >= 0.7) {
          autoSelected.add(issue.file.path);
        }
      }
      onSelectionChange(autoSelected);
      setPhase('done');
    } catch (err) {
      setPhase('error');
      setErrorMsg(err instanceof Error ? err.message : 'Detection failed');
    }
  }, [scanPath, onAiOrientIssuesChange, onSelectionChange]);

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
    if (on) {
      onSelectionChange(new Set(aiOrientIssues.map(i => i.file.path)));
    } else {
      onSelectionChange(new Set());
    }
  }, [aiOrientIssues, onSelectionChange]);

  const handleSelectHighConfidence = useCallback(() => {
    const highConf = new Set<string>();
    for (const issue of aiOrientIssues) {
      if (issue.confidence >= 0.8) {
        highConf.add(issue.file.path);
      }
    }
    onSelectionChange(highConf);
  }, [aiOrientIssues, onSelectionChange]);

  const confidenceColor = (c: number): string => {
    if (c >= 0.9) return '#22c55e';  // green
    if (c >= 0.7) return '#f59e0b';  // amber
    return '#ef4444';                 // red
  };

  const confidenceBadge = (c: number): { label: string; title: string } => {
    if (c >= 0.9) return { label: 'High', title: 'Highly confident. Safe to auto-apply.' };
    if (c >= 0.7) return { label: 'Medium', title: 'Fairly confident. Review recommended.' };
    return { label: 'Low', title: 'Uncertain. Please verify before applying.' };
  };

  const rotationLabel = (r: number): string => `\u2192 ${r}\u00B0 CW`;

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Orientation Detection</h2>
          <span className="d-meta">
            {hasScanned
              ? scannedCount > 0
                ? `${aiOrientIssues.length} issues found in ${scannedCount} files`
                : `${aiOrientIssues.length} issues found`
              : 'Detect photos that need rotation correction'}
          </span>
        </div>

        {/* Pre-scan: trigger */}
        {!hasScanned && (
          <div className="orient-setup">
            <div className="d-sub">
              Scans each photo/video to detect if it needs rotation correction.
              Uses a local AI model — no API key required.
            </div>

            {phase === 'error' && (
              <div className="orient-error">{errorMsg}</div>
            )}

            {phase === 'scanning' && (
              <div className="orient-progress">
                <div className="exec-pbar">
                  <div
                    className="exec-pfill"
                    style={{
                      width: progressTotal > 0 ? `${(progressCurrent / progressTotal) * 100}%` : '0%',
                      transition: 'width 0.3s ease',
                    }}
                  />
                </div>
                <div className="orient-progress-text">
                  {progressTotal > 0
                    ? `Scanning ${progressCurrent} / ${progressTotal}: ${progressFilename}`
                    : 'Loading model...'}
                </div>
              </div>
            )}

            <button
              className="btn-mark-done"
              onClick={() => { void handleDetect(); }}
              disabled={phase === 'scanning'}
              style={{ marginTop: '1rem' }}
            >
              {phase === 'scanning' ? 'Detecting...' : 'Detect orientation issues'}
            </button>
          </div>
        )}

        {/* Post-scan: results */}
        {hasScanned && aiOrientIssues.length === 0 && (
          <div className="d-sub">
            <span className="safe">
              {'\u2713'} Scanned {scannedCount} files. All orientations look correct.
            </span>
          </div>
        )}

        {hasScanned && aiOrientIssues.length > 0 && (
          <>
            <div className="d-sub">
              Detected {aiOrientIssues.length} files may need rotation (out of {scannedCount} scanned).{' '}
              <span className="safe">JPEG: lossless pixel rotation. Video: metadata only. Others: Pillow rotation.</span>
              <br />
              <span style={{ color: 'var(--dim)', fontSize: '0.85rem' }}>
                Items with {'\u2265'}70% confidence are auto-selected. You can adjust below.
              </span>
            </div>

            <div className="bulk-bar">
              <span className="bl">Selection:</span>
              <button
                className={`bbtn${selectedPaths.size === aiOrientIssues.length ? ' on' : ''}`}
                onClick={() => handleSelectAll(true)}
              >
                Select all
              </button>
              <button
                className="bbtn"
                onClick={handleSelectHighConfidence}
              >
                High confidence only
              </button>
              <button
                className={`bbtn${selectedPaths.size === 0 ? ' on' : ''}`}
                onClick={() => handleSelectAll(false)}
              >
                Clear all
              </button>
              <div className="bulk-spacer" />
              <span className="bulk-stat">
                {selectedPaths.size} of {aiOrientIssues.length} selected
              </span>
            </div>

            <div className="orient-grid">
              {aiOrientIssues.map((issue, idx) => {
                const isSelected = selectedPaths.has(issue.file.path);
                const badge = confidenceBadge(issue.confidence);
                const thumbUrl = `/api/thumbnail?path=${encodeURIComponent(issue.file.path)}`;
                return (
                  <div
                    key={issue.file.path}
                    className={`orient-item${isSelected ? ' selected' : ' unselected'}`}
                    onClick={() => setLightboxIndex(idx)}
                  >
                    <div
                      className="orient-checkbox"
                      onClick={(e) => { e.stopPropagation(); toggleFile(issue.file.path); }}
                    >
                      {isSelected ? '\u2611' : '\u2610'}
                    </div>
                    <div className="orient-compare">
                      <div className="orient-compare-col">
                        <div className="orient-compare-label">Before</div>
                        <img
                          className="orient-compare-img"
                          src={thumbUrl}
                          alt="Before"
                          loading="lazy"
                        />
                      </div>
                      <div className="orient-compare-arrow">{'\u2192'}</div>
                      <div className="orient-compare-col">
                        <div className="orient-compare-label">After</div>
                        <img
                          className="orient-compare-img"
                          src={thumbUrl}
                          alt="After"
                          loading="lazy"
                          style={{ transform: `rotate(${issue.rotation}deg)` }}
                        />
                      </div>
                    </div>
                    <div className="orient-label">
                      <span className="orient-rotation">{rotationLabel(issue.rotation)}</span>
                      <span
                        className="orient-confidence"
                        style={{ color: confidenceColor(issue.confidence) }}
                        title={badge.title}
                      >
                        {badge.label} {Math.round(issue.confidence * 100)}%
                      </span>
                    </div>
                    <div className="orient-filename">{issue.file.name}</div>
                  </div>
                );
              })}
            </div>

            {/* Lightbox modal */}
            {lightboxIndex !== null && (() => {
              const issue = aiOrientIssues[lightboxIndex];
              if (!issue) return null;
              const isSelected = selectedPaths.has(issue.file.path);
              const badge = confidenceBadge(issue.confidence);
              const thumbUrl = `/api/thumbnail?path=${encodeURIComponent(issue.file.path)}&size=800`;
              return (
                <div className="orient-lightbox" onClick={() => setLightboxIndex(null)}>
                  <div className="orient-lb-content" onClick={(e) => e.stopPropagation()}>
                    <button className="orient-lb-close" onClick={() => setLightboxIndex(null)}>
                      {'\u2715'}
                    </button>
                    <div className="orient-lb-compare">
                      <div className="orient-lb-col">
                        <div className="orient-compare-label">Before</div>
                        <img className="orient-lb-img" src={thumbUrl} alt="Before" />
                      </div>
                      <div className="orient-lb-arrow">{'\u2192'}</div>
                      <div className="orient-lb-col">
                        <div className="orient-compare-label">After</div>
                        <img
                          className="orient-lb-img"
                          src={thumbUrl}
                          alt="After"
                          style={{ transform: `rotate(${issue.rotation}deg)` }}
                        />
                      </div>
                    </div>
                    <div className="orient-lb-info">
                      <span className="orient-lb-filename">{issue.file.name}</span>
                      <span className="orient-rotation">{rotationLabel(issue.rotation)}</span>
                      <span
                        className="orient-confidence"
                        style={{ color: confidenceColor(issue.confidence) }}
                      >
                        {badge.label} {Math.round(issue.confidence * 100)}%
                      </span>
                    </div>
                    <div className="orient-lb-actions">
                      <button
                        className={`orient-lb-toggle${isSelected ? ' on' : ''}`}
                        onClick={() => toggleFile(issue.file.path)}
                      >
                        {isSelected ? '\u2611 Selected for rotation' : '\u2610 Not selected'}
                      </button>
                      <div className="orient-lb-nav">
                        <button
                          disabled={lightboxIndex <= 0}
                          onClick={() => setLightboxIndex(lightboxIndex - 1)}
                        >
                          {'\u2190'} Prev
                        </button>
                        <span>{lightboxIndex + 1} / {aiOrientIssues.length}</span>
                        <button
                          disabled={lightboxIndex >= aiOrientIssues.length - 1}
                          onClick={() => setLightboxIndex(lightboxIndex + 1)}
                        >
                          Next {'\u2192'}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}
          </>
        )}

        <div className="d-done-bar">
          <div className="ddb-info">
            {hasScanned && aiOrientIssues.length > 0 ? (
              <><strong>{selectedPaths.size} files</strong> will be auto-rotated.</>
            ) : hasScanned ? (
              <span>No orientation issues found.</span>
            ) : (
              <span>Run detection to find orientation issues.</span>
            )}
          </div>
          <button className="btn-mark-done" onClick={onDone}>
            {'\u2713'} Done reviewing
          </button>
        </div>
      </div>
    </div>
  );
}
