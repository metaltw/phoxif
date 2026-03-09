export interface FileInfo {
  path: string;
  name: string;
  size: number;
  date: string | null;
  gps: { lat: number; lon: number } | null;
  orientation: number | null;
  codec: string | null;
  extension: string;
  width: number | null;
  height: number | null;
}

export interface DuplicateGroup {
  id: number;
  reason: string;
  files: FileInfo[];
  keep_index: number;
}

export interface SimilarGroup {
  id: number;
  files: FileInfo[];
  keep_index: number;
  similarities: Array<{ i: number; j: number; distance: number; similarity: number }>;
  reason: 'burst' | 'similar';
  reclaimable_size: number;
}

export interface OrientationIssue {
  file: FileInfo;
  current_orientation: number;
  label: string;
}

export interface RenamePreview {
  file: FileInfo;
  old_name: string;
  new_name: string;
  new_path: string;
}

export interface ScanResult {
  total_files: number;
  total_size: number;
  base_dir: string;
  duplicates: DuplicateGroup[];
  similar_groups: SimilarGroup[];
  orientation_issues: OrientationIssue[];
  rename_preview: RenamePreview[];
}

export interface Operation {
  type: 'trash' | 'rename' | 'gps' | 'convert' | 'orientation';
  file: string;
  old_value: string;
  new_value: string;
  detail: string;
}

export interface Session {
  timestamp: string;
  operations: Operation[];
  undone: boolean;
}

export type Screen = 'scan' | 'review' | 'duplicates' | 'similar' | 'orientation' | 'rename' | 'confirm' | 'execute' | 'done' | 'history';

export type ThumbState = 'keep' | 'trash' | 'neutral';
