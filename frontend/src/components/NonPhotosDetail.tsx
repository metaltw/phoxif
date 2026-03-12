import React, { useState, useCallback, useMemo } from 'react';
import type { NonPhotoItem } from '../types';

interface NonPhotosDetailProps {
  nonPhotos: NonPhotoItem[];
  selectedPaths: Set<string>;
  onSelectionChange: (selected: Set<string>) => void;
  onBack: () => void;
  onDone: () => void;
  formatSize: (bytes: number) => string;
}

const CATEGORY_LABELS: Record<string, string> = {
  screenshot: 'Screenshots',
  screen_recording: 'Screen Recordings',
  messaging: 'Messaging Images',
  document: 'Document Photos',
};

const CATEGORY_ICONS: Record<string, string> = {
  screenshot: '\uD83D\uDCF1',
  screen_recording: '\uD83C\uDFA5',
  messaging: '\uD83D\uDCAC',
  document: '\uD83D\uDCC4',
};

export function NonPhotosDetail({
  nonPhotos,
  selectedPaths,
  onSelectionChange,
  onBack,
  onDone,
  formatSize,
}: NonPhotosDetailProps): React.JSX.Element {
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  // Group by category (memoized)
  const grouped = useMemo(() => {
    const map = new Map<string, NonPhotoItem[]>();
    for (const item of nonPhotos) {
      const list = map.get(item.category) ?? [];
      list.push(item);
      map.set(item.category, list);
    }
    return map;
  }, [nonPhotos]);

  const categories = useMemo(() =>
    ['screenshot', 'screen_recording', 'messaging', 'document']
      .filter(cat => grouped.has(cat)),
    [grouped],
  );

  const totalSelectedSize = useMemo(() => {
    let size = 0;
    for (const item of nonPhotos) {
      if (selectedPaths.has(item.file.path)) {
        size += item.file.size;
      }
    }
    return size;
  }, [nonPhotos, selectedPaths]);

  const toggleExpand = useCallback((cat: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  }, []);

  const toggleFile = useCallback((path: string) => {
    const next = new Set(selectedPaths);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    onSelectionChange(next);
  }, [selectedPaths, onSelectionChange]);

  const toggleCategory = useCallback((category: string, on: boolean) => {
    const next = new Set(selectedPaths);
    const items = grouped.get(category) ?? [];
    for (const item of items) {
      if (on) {
        next.add(item.file.path);
      } else {
        next.delete(item.file.path);
      }
    }
    onSelectionChange(next);
  }, [selectedPaths, onSelectionChange, grouped]);

  const handleSelectAll = useCallback((on: boolean) => {
    if (on) {
      onSelectionChange(new Set(nonPhotos.map(i => i.file.path)));
    } else {
      onSelectionChange(new Set());
    }
  }, [nonPhotos, onSelectionChange]);

  return (
    <div className="screen">
      <div className="detail-wrap">
        <button className="d-back" onClick={onBack}>
          {'\u2190'} Back to summary
        </button>
        <div className="d-head">
          <h2>Non-Photo Files</h2>
          <span className="d-meta">
            {nonPhotos.length} files detected across {categories.length} {categories.length === 1 ? 'category' : 'categories'}
          </span>
        </div>

        <div className="d-sub">
          These files appear to be screenshots, screen recordings, messaging app images,
          or document photos. Selected files will be moved to <code>_non_photos/</code> subfolders.
          <br />
          <span style={{ color: 'var(--dim)', fontSize: '0.85rem' }}>
            Files are NOT deleted — just organized into separate folders.
          </span>
        </div>

        <div className="bulk-bar">
          <span className="bl">Selection:</span>
          <button
            className={`bbtn${selectedPaths.size === nonPhotos.length ? ' on' : ''}`}
            onClick={() => handleSelectAll(true)}
          >
            Select all
          </button>
          <button
            className={`bbtn${selectedPaths.size === 0 ? ' on' : ''}`}
            onClick={() => handleSelectAll(false)}
          >
            Clear all
          </button>
          <div className="bulk-spacer" />
          <span className="bulk-stat">
            {selectedPaths.size} of {nonPhotos.length} selected
            {totalSelectedSize > 0 && ` \u00B7 ${formatSize(totalSelectedSize)}`}
          </span>
        </div>

        <div className="nonphoto-categories">
          {categories.map(cat => {
            const items = grouped.get(cat) ?? [];
            const selectedInCat = items.filter(i => selectedPaths.has(i.file.path)).length;
            const allSelected = selectedInCat === items.length;
            const isExpanded = expandedCategories.has(cat);
            const catSize = items.reduce((sum, i) => sum + i.file.size, 0);
            const thumbUrl = (path: string) => `/api/thumbnail?path=${encodeURIComponent(path)}`;

            return (
              <div key={cat} className="nonphoto-cat">
                <div
                  className="nonphoto-cat-header"
                  onClick={() => toggleExpand(cat)}
                >
                  <span className="nonphoto-cat-icon">{CATEGORY_ICONS[cat]}</span>
                  <span className="nonphoto-cat-title">{CATEGORY_LABELS[cat]}</span>
                  <span className="nonphoto-cat-count">
                    {items.length} files &middot; {formatSize(catSize)}
                  </span>
                  <span className="nonphoto-cat-selected">
                    {selectedInCat} selected
                  </span>
                  <button
                    className={`bbtn small${allSelected ? ' on' : ''}`}
                    onClick={(e) => { e.stopPropagation(); toggleCategory(cat, !allSelected); }}
                  >
                    {allSelected ? 'Deselect' : 'Select'} all
                  </button>
                  <span className="nonphoto-cat-chevron">
                    {isExpanded ? '\u25BC' : '\u25B6'}
                  </span>
                </div>

                {isExpanded && (
                  <div className="nonphoto-cat-files">
                    {items.map(item => {
                      const isSelected = selectedPaths.has(item.file.path);
                      return (
                        <div
                          key={item.file.path}
                          className={`nonphoto-file${isSelected ? ' selected' : ' unselected'}`}
                          onClick={() => toggleFile(item.file.path)}
                        >
                          <div className="nonphoto-checkbox">
                            {isSelected ? '\u2611' : '\u2610'}
                          </div>
                          <img
                            className="nonphoto-thumb"
                            src={thumbUrl(item.file.path)}
                            alt={item.file.name}
                            loading="lazy"
                          />
                          <div className="nonphoto-info">
                            <div className="nonphoto-filename">{item.file.name}</div>
                            <div className="nonphoto-reason">
                              {item.reason} &middot; {formatSize(item.file.size)}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="d-done-bar">
          <div className="ddb-info">
            {selectedPaths.size > 0 ? (
              <>
                <strong>{selectedPaths.size} files</strong> ({formatSize(totalSelectedSize)}) will be moved to _non_photos/ subfolders.
              </>
            ) : (
              <span>No files selected for moving.</span>
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
