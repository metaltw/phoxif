import React, { useState, useEffect } from 'react';
import type { FileInfo, ThumbState } from '../types';

interface ThumbnailProps {
  file: FileInfo;
  state: ThumbState;
  onClick: () => void;
  formatSize: (bytes: number) => string;
  gradientColor: string;
}

// Generate a stable gradient from filename hash
export function hashColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash) % 360;
  return `linear-gradient(135deg, hsl(${hue}, 40%, 18%), hsl(${(hue + 30) % 360}, 50%, 25%))`;
}

// File extensions that have thumbnail support (backend handles conversion)
const PREVIEWABLE = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'heic', 'heif', 'mov', 'mp4', 'avi', 'mkv']);

function canPreview(ext: string): boolean {
  return PREVIEWABLE.has(ext.toLowerCase().replace('.', ''));
}

export function Thumbnail({ file, state, onClick, formatSize, gradientColor }: ThumbnailProps): React.JSX.Element {
  const className = `thumb${state === 'keep' ? ' keep' : ''}${state === 'trash' ? ' trash' : ''}`;
  const [imgError, setImgError] = useState(false);
  useEffect(() => { setImgError(false); }, [file.path]);
  const showRealImage = canPreview(file.extension) && file.path && !imgError;
  const thumbUrl = `/api/thumbnail?path=${encodeURIComponent(file.path)}`;

  return (
    <div className={className} onClick={onClick}>
      <span className="t-badge">
        {state === 'keep' ? 'KEEP' : state === 'trash' ? 'TRASH' : ''}
      </span>
      <div className="t-check">
        {state === 'keep' ? '\u2713' : state === 'trash' ? '\u2212' : ''}
      </div>
      <div className="t-img" style={{ background: gradientColor, position: 'relative', overflow: 'hidden' }}>
        {showRealImage ? (
          <img
            src={thumbUrl}
            alt={file.name}
            onError={() => setImgError(true)}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: '100%',
              objectFit: 'cover',
            }}
          />
        ) : (
          <span style={{
            fontSize: '13px',
            color: 'rgba(255,255,255,0.2)',
            textTransform: 'uppercase',
            fontWeight: 600,
            letterSpacing: '1px',
          }}>
            {file.extension.replace('.', '')}
          </span>
        )}
      </div>
      <div className="t-info">
        <div className="t-name">{file.name}</div>
        <div className="t-meta">{formatSize(file.size)}{file.date ? ` \u00B7 ${file.date}` : ''}</div>
      </div>
    </div>
  );
}
