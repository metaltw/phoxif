import React from 'react';
import type { DuplicateGroup as DupGroupType, ThumbState } from '../types';
import { Thumbnail, hashColor } from './Thumbnail';
import { revealInFinder } from '../api';

interface DuplicateGroupProps {
  group: DupGroupType;
  groupIndex: number;
  states: ThumbState[];
  onToggle: (fileIndex: number) => void;
  formatSize: (bytes: number) => string;
}

export function DuplicateGroupComponent({
  group,
  groupIndex,
  states,
  onToggle,
  formatSize,
}: DuplicateGroupProps): React.JSX.Element {
  let reclaimable = 0;
  states.forEach((state, i) => {
    if (state === 'trash') reclaimable += group.files[i].size;
  });

  const gradientColor = hashColor(group.files[0].name);
  // Get folder path from first file
  const folderPath = group.files[0].path.substring(0, group.files[0].path.lastIndexOf('/'));

  return (
    <div className="grp">
      <div className="grp-head">
        <span className="gl">Group {groupIndex + 1}</span>
        <span className="gm">
          {group.files.length} identical &middot; {formatSize(reclaimable)} reclaimable
        </span>
        <button
          className="grp-finder"
          onClick={(e) => { e.stopPropagation(); void revealInFinder(folderPath); }}
          title="Show in Finder"
        >
          Finder {'\u2197'}
        </button>
        <span className="gr">{group.reason}</span>
      </div>
      <div className="grp-body">
        {group.files.map((file, i) => (
          <Thumbnail
            key={file.path}
            file={file}
            state={states[i]}
            onClick={() => onToggle(i)}
            formatSize={formatSize}
            gradientColor={gradientColor}
          />
        ))}
      </div>
    </div>
  );
}
