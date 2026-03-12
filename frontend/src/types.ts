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
  rotation: number;       // 90, 180, 270
  confidence: number;     // 0.0-1.0
}

export interface RenamePreview {
  file: FileInfo;
  old_name: string;
  new_name: string;
  new_path: string;
}

export interface DateMismatch {
  file: FileInfo;
  exif_date: string;
  file_mtime: string;
  source: 'exif' | 'filename';
}

export interface NonPhotoItem {
  file: FileInfo;
  category: 'screenshot' | 'screen_recording' | 'messaging' | 'document';
  reason: string;
}

export interface ScanResult {
  total_files: number;
  total_size: number;
  base_dir: string;
  duplicates: DuplicateGroup[];
  similar_groups: SimilarGroup[];
  orientation_issues: OrientationIssue[];
  rename_preview: RenamePreview[];
  date_mismatches: DateMismatch[];
  non_photos: NonPhotoItem[];
}

export interface Operation {
  type: 'trash' | 'rename' | 'gps' | 'convert' | 'orientation' | 'auto-rotate' | 'fix-dates' | 'move-non-photos';
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

export type Screen = 'scan' | 'review' | 'duplicates' | 'similar' | 'orientation' | 'rename' | 'dates' | 'non-photos' | 'confirm' | 'execute' | 'done' | 'history';

export type ThumbState = 'keep' | 'trash' | 'neutral';
