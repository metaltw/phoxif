import React from 'react';
import type { Screen, ScanResult, ThumbState } from '../types';
import { SummaryCard } from './SummaryCard';

interface ReviewScreenProps {
  scanResult: ScanResult;
  reviewedCategories: Set<string>;
  skippedCategories: Set<string>;
  onToggleSkip: (category: string) => void;
  dupStates: Map<number, ThumbState[]>;
  simStates: Map<number, ThumbState[]>;
  orientSelected: Set<string>;
  renameSelected: Set<string>;
  onNavigate: (screen: Screen) => void;
  formatSize: (bytes: number) => string;
}

export function ReviewScreen({
  scanResult,
  reviewedCategories,
  skippedCategories,
  onToggleSkip,
  dupStates,
  simStates,
  orientSelected,
  renameSelected,
  onNavigate,
  formatSize,
}: ReviewScreenProps): React.JSX.Element {
  // Duplicate stats
  const dupCount = scanResult.duplicates.reduce((sum, g) => sum + g.files.length, 0);
  const dupGroups = scanResult.duplicates.length;
  let dupReclaimable = 0;
  let dupTrashCount = 0;
  for (const group of scanResult.duplicates) {
    const states = dupStates.get(group.id);
    if (!states) continue;
    states.forEach((state, i) => {
      if (state === 'trash') {
        dupReclaimable += group.files[i].size;
        dupTrashCount++;
      }
    });
  }

  // Similar stats
  const simCount = scanResult.similar_groups.reduce((sum, g) => sum + g.files.length, 0);
  const simGroups = scanResult.similar_groups.length;
  let simReclaimable = 0;
  let simTrashCount = 0;
  for (const group of scanResult.similar_groups) {
    const states = simStates.get(group.id);
    if (!states) continue;
    states.forEach((state, i) => {
      if (state === 'trash') {
        simReclaimable += group.files[i].size;
        simTrashCount++;
      }
    });
  }

  // Orientation stats
  const orientCount = scanResult.orientation_issues.length;

  // Rename stats
  const renameCount = scanResult.rename_preview.length;

  const activeCount = [...reviewedCategories].filter(c => !skippedCategories.has(c)).length;

  return (
    <div className="screen">
      <div className="review-wrap">
        <div className="review-top">
          <h2>Scan Results</h2>
          <span className="review-info">
            {scanResult.total_files} files &middot; {formatSize(scanResult.total_size)} &middot; {scanResult.base_dir}
          </span>
        </div>
        <div className="review-sub">
          Click a card to review details. Nothing changes until you confirm.
        </div>

        <div className="sgrid">
          <SummaryCard
            icon={'\uD83D\uDCCB'}
            title="Duplicate Files"
            count={dupCount}
            description={
              dupCount > 0 ? (
                <>
                  <strong>{dupGroups} groups</strong> of identical files<br />
                  Can free up <strong>{formatSize(dupReclaimable)}</strong>
                </>
              ) : (
                <span className="safe">No duplicates found</span>
              )
            }
            action={dupCount > 0 ? 'Review duplicates \u2192' : ''}
            reviewed={reviewedCategories.has('duplicates')}
            skipped={skippedCategories.has('duplicates')}
            noIssue={dupCount === 0}
            onClick={dupCount > 0 ? () => onNavigate('duplicates') : undefined}
            onSkip={() => onToggleSkip('duplicates')}
          />
          <SummaryCard
            icon={'\uD83D\uDD0D'}
            title="Similar Photos"
            count={simCount}
            description={
              simCount > 0 ? (
                <>
                  <strong>{simGroups} groups</strong> of similar photos<br />
                  Can free up <strong>{formatSize(simReclaimable)}</strong>
                </>
              ) : (
                <span className="safe">No similar photos found</span>
              )
            }
            action={simCount > 0 ? 'Review similar \u2192' : ''}
            reviewed={reviewedCategories.has('similar')}
            skipped={skippedCategories.has('similar')}
            noIssue={simCount === 0}
            onClick={simCount > 0 ? () => onNavigate('similar') : undefined}
            onSkip={() => onToggleSkip('similar')}
          />
          <SummaryCard
            icon={'\u270F\uFE0F'}
            title="Rename by Date"
            count={renameCount}
            description={
              renameCount > 0 ? (
                <>
                  <strong>{renameCount} files</strong> can be renamed to date format<br />
                  {renameSelected.size} selected
                </>
              ) : (
                <span className="safe">All files already named by date</span>
              )
            }
            action={renameCount > 0 ? 'Preview renames \u2192' : ''}
            reviewed={reviewedCategories.has('rename')}
            skipped={skippedCategories.has('rename')}
            noIssue={renameCount === 0}
            onClick={renameCount > 0 ? () => onNavigate('rename') : undefined}
            onSkip={() => onToggleSkip('rename')}
          />
          <SummaryCard
            icon={'\uD83D\uDD04'}
            title="Orientation Fix"
            count={orientCount}
            description={
              orientCount > 0 ? (
                <>
                  <strong>{orientCount} photos</strong> need rotation fix<br />
                  {orientSelected.size} selected
                </>
              ) : (
                <span className="safe">All orientations are correct</span>
              )
            }
            action={orientCount > 0 ? 'Review orientation \u2192' : ''}
            reviewed={reviewedCategories.has('orientation')}
            skipped={skippedCategories.has('orientation')}
            noIssue={orientCount === 0}
            onClick={orientCount > 0 ? () => onNavigate('orientation') : undefined}
            onSkip={() => onToggleSkip('orientation')}
          />
          <SummaryCard
            icon={'\uD83C\uDFAC'}
            title="Video Compression"
            count={0}
            description={<span className="safe">Coming soon</span>}
            action=""
            reviewed={false}
            skipped={false}
            noIssue={true}
          />
          <SummaryCard
            icon={'\uD83D\uDCC5'}
            title="File Dates"
            count={'\u2713'}
            description={<span className="safe">All dates match EXIF</span>}
            action=""
            reviewed={false}
            skipped={false}
            noIssue={true}
          />
        </div>

        {activeCount > 0 && (
          <div className="review-execute-bar">
            <div className="reb-text">
              {'\u2713'} <strong>{activeCount}</strong> {activeCount === 1 ? 'category' : 'categories'} ready to execute.
            </div>
            <button className="btn-execute" onClick={() => onNavigate('confirm')}>
              Review &amp; Confirm {'\u2192'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
