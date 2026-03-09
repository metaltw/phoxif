import React, { useState, useRef, useCallback } from 'react';

interface ConfirmOperation {
  key: string;
  icon: string;
  label: string;
  detail: string;
  files: string[];
}

interface ConfirmScreenProps {
  operations: ConfirmOperation[];
  confirmToggles: Record<string, boolean>;
  onToggle: (key: string) => void;
  onReorder: (orderedKeys: string[]) => void;
  onBack: () => void;
  onExecute: () => void;
}

export function ConfirmScreen({
  operations,
  confirmToggles,
  onToggle,
  onReorder,
  onBack,
  onExecute,
}: ConfirmScreenProps): React.JSX.Element {
  const activeCount = operations.filter(op => confirmToggles[op.key] !== false).length;

  // Drag state
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  const dragNode = useRef<HTMLDivElement | null>(null);

  const handleDragStart = useCallback((e: React.DragEvent<HTMLDivElement>, idx: number) => {
    dragNode.current = e.currentTarget;
    setDragIdx(idx);
    e.dataTransfer.effectAllowed = 'move';
    // Make ghost semi-transparent
    requestAnimationFrame(() => {
      if (dragNode.current) dragNode.current.style.opacity = '0.4';
    });
  }, []);

  const handleDragEnd = useCallback(() => {
    if (dragNode.current) dragNode.current.style.opacity = '1';
    if (dragIdx !== null && overIdx !== null && dragIdx !== overIdx) {
      const reordered = [...operations];
      const [moved] = reordered.splice(dragIdx, 1);
      reordered.splice(overIdx, 0, moved);
      onReorder(reordered.map(op => op.key));
    }
    setDragIdx(null);
    setOverIdx(null);
    dragNode.current = null;
  }, [dragIdx, overIdx, operations, onReorder]);

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>, idx: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setOverIdx(idx);
  }, []);

  let activeStep = 0;

  return (
    <div className="screen-center">
      <div className="confirm-wrap">
        <div className="cbox">
          <div className="cbox-head">
            <h2>Review Changes</h2>
            <p>Drag to reorder. Toggle off to skip.</p>
          </div>
          <div className="clist">
            {operations.map((op, idx) => {
              const isOn = confirmToggles[op.key] !== false;
              if (isOn) activeStep++;
              const stepNum = isOn ? activeStep : null;
              const isDragging = dragIdx === idx;
              const isOver = overIdx === idx && dragIdx !== idx;

              return (
                <div
                  className={`citem${isOn ? '' : ' citem-off'}${isOver ? ' citem-drop-target' : ''}`}
                  key={op.key}
                  draggable
                  onDragStart={(e) => handleDragStart(e, idx)}
                  onDragEnd={handleDragEnd}
                  onDragOver={(e) => handleDragOver(e, idx)}
                  style={isDragging ? { opacity: 0.4 } : undefined}
                >
                  <div className="ci-drag" title="Drag to reorder">{'\u2261'}</div>
                  <div className="ci-step">{stepNum ?? '\u2013'}</div>
                  <div className="ci-icon">{op.icon}</div>
                  <div className="ci-body">
                    <div className="ci-text"><strong>{op.label}</strong></div>
                    <div className="ci-detail">{op.detail}</div>
                  </div>
                  <div
                    className={`toggle${isOn ? '' : ' off'}`}
                    onClick={() => onToggle(op.key)}
                  />
                </div>
              );
            })}
            {operations.length === 0 && (
              <div className="citem">
                <div className="ci-body">
                  <div className="ci-text" style={{ color: 'var(--dim)' }}>
                    No actions to execute. Go back and review some categories.
                  </div>
                </div>
              </div>
            )}
          </div>
          <div className="csafety">
            <strong>{'\uD83D\uDEE1\uFE0F'} Safety guarantees:</strong><br />
            {'\u2022'} Removed files {'\u2192'} system Trash (recover anytime in Finder)<br />
            {'\u2022'} Video originals kept alongside compressed versions<br />
            {'\u2022'} EXIF changes logged for undo<br />
            {'\u2022'} Full operation log {'\u2192'} <code>.phoxif_log.json</code>
          </div>
          <div className="cactions">
            <button className="btn-back" onClick={onBack}>{'\u2190'} Back</button>
            <button
              className="btn-go"
              onClick={onExecute}
              disabled={activeCount === 0}
            >
              Execute {activeCount} action{activeCount !== 1 ? 's' : ''}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
