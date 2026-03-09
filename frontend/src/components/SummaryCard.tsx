import React from 'react';

interface SummaryCardProps {
  icon: string;
  title: string;
  count: number | string;
  description: React.ReactNode;
  action: string;
  reviewed: boolean;
  skipped: boolean;
  noIssue: boolean;
  onClick?: () => void;
  onSkip?: () => void;
}

export function SummaryCard({
  icon,
  title,
  count,
  description,
  action,
  reviewed,
  skipped,
  noIssue,
  onClick,
  onSkip,
}: SummaryCardProps): React.JSX.Element {
  const className = `scard${reviewed ? ' reviewed' : ''}${skipped ? ' skipped' : ''}${noIssue ? ' no-issue' : ''}`;

  return (
    <div className={className} onClick={noIssue ? undefined : onClick}>
      <div className="sc-top">
        <div className="sc-icon">{icon}</div>
        <div className="sc-title">{title}</div>
        <div className="sc-count">{noIssue ? '\u2713' : count}</div>
      </div>
      <div className="sc-body">{description}</div>
      {!noIssue && (
        <div className="sc-action">
          {skipped ? (
            <span
              className="sc-skip-label"
              onClick={(e) => { e.stopPropagation(); onSkip?.(); }}
            >
              Skipped &middot; <span className="sc-undo">Undo</span>
            </span>
          ) : reviewed ? (
            <>
              {'\u2713 Reviewed'}
              <span
                className="sc-skip-btn"
                onClick={(e) => { e.stopPropagation(); onSkip?.(); }}
                title="Skip this category"
              >
                Skip
              </span>
            </>
          ) : action}
        </div>
      )}
    </div>
  );
}
