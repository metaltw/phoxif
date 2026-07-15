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

export type IntakeMode = 'rescue' | 'inbox';

export interface SourceSummary {
  path: string;
  label: string;
  total_files: number;
  total_size: number;
  photo_count: number;
  video_count: number;
}

export interface ScanResult {
  mode: IntakeMode;
  total_files: number;
  total_size: number;
  base_dir: string;
  source_paths: string[];
  sources: SourceSummary[];
  ready_to_collect: number;
  missing_dates: number;
  messaging_files: number;
  duplicates: DuplicateGroup[];
  similar_groups: SimilarGroup[];
  orientation_issues: OrientationIssue[];
  rename_preview: RenamePreview[];
  date_mismatches: DateMismatch[];
  non_photos: NonPhotoItem[];
}

export interface IntakeBatchResult {
  batch_id: string;
  source_id: string;
  mode: IntakeMode;
  scanned: number;
  new_files: number;
  new_sightings: number;
  already_known: number;
  archived_reunions: number;
  staged_files: number;
  verified_staging: number;
  phash_failures: number;
  sidecars: number;
  staged_sidecars: number;
  total_bytes: number;
}

export interface IntakeIngestSummary {
  mode: IntakeMode;
  complete: boolean;
  batches: IntakeBatchResult[];
  failures: Array<{
    source_path: string;
    label: string;
    error: string;
  }>;
  totals: Omit<IntakeBatchResult, 'batch_id' | 'source_id' | 'mode'>;
}

export interface DedupeCandidate {
  sha256: string;
  path: string;
  name: string;
  source_id: string;
  original_dir: string;
  size: number;
  width: number | null;
  height: number | null;
  phash: string;
  status: string;
  native_date: string | null;
  has_gps: boolean;
  mtime_epoch: number;
  pixels: number;
}

export interface DedupePair {
  id: string;
  distance: number;
  reason: string;
  files: DedupeCandidate[];
  winner_sha256?: string;
  loser_sha256?: string;
}

export interface DedupeBatchResult {
  batch_id: string;
  exact_groups: Array<{ sha256: string; copies: number }>;
  auto_groups: DedupePair[];
  review_pairs: DedupePair[];
  burst_pairs: DedupePair[];
  protected_edits: DedupePair[];
}

export interface DedupeSummary {
  complete: boolean;
  results: DedupeBatchResult[];
  failures: Array<{ batch_id: string; error: string }>;
}

export interface DedupeResolution {
  decision: string;
  pair_id: string;
  refreshed_result: DedupeBatchResult;
}

export interface PendingTrashItem {
  operation_id: number;
  batch_id: string;
  sha256: string;
  reason: string;
  paths: string[];
  names: string[];
  kept_sha256: string | null;
}

export interface TrashExecutionSummary {
  completed: number;
  failed: number;
  results: Array<{
    operation_id: number;
    status: 'completed' | 'failed';
    paths: string[];
    failures?: Array<{ path: string; error: string }>;
  }>;
}

export interface DateEvidence {
  value: string;
  exif_value: string;
  source: string;
  confidence: number;
  estimated: boolean;
  precision: string | null;
  keywords: string[];
}

export interface DatePlanItem {
  batch_id: string;
  sha256: string;
  path: string | null;
  name: string;
  media_type: string;
  action: 'keep-native' | 'write-estimated' | 'quarantine' | 'skip';
  evidence: DateEvidence | null;
  reason: string;
}

export interface DateBatchPlan {
  batch_id: string;
  items: DatePlanItem[];
  counts: Record<'keep-native' | 'write-estimated' | 'quarantine' | 'skip', number>;
}

export interface DatePlanSummary {
  complete: boolean;
  plans: DateBatchPlan[];
  failures: Array<{ batch_id: string; error: string }>;
}

export interface DateExecutionSummary {
  complete: boolean;
  results: Array<{
    batch_id: string;
    completed: number;
    failed: number;
    results: Array<{ sha256: string; status: string; reason?: string; error?: string; written?: boolean }>;
  }>;
  failures: Array<{ batch_id: string; error: string }>;
}

export interface GpsEvidence {
  latitude: number;
  longitude: number;
  source: 'folder-mapping' | 'temporal-neighbor';
  estimated: boolean;
  reference_sha256: string[];
  offset_seconds: number | null;
  folder_key: string | null;
  keywords: string[];
}

export interface GpsPlanItem {
  batch_id: string;
  sha256: string;
  path: string | null;
  name: string;
  media_type: string;
  action: 'keep-native' | 'keep-backfilled' | 'write-mapped' | 'write-neighbor' | 'skip';
  evidence: GpsEvidence | null;
  reason: string;
}

export interface GpsBatchPlan {
  batch_id: string;
  timezone_name: string;
  items: GpsPlanItem[];
  counts: Record<'keep-native' | 'keep-backfilled' | 'write-mapped' | 'write-neighbor' | 'skip', number>;
}

export interface GpsPlanSummary {
  complete: boolean;
  plans: GpsBatchPlan[];
  failures: Array<{ batch_id: string; error: string }>;
}

export interface GpsExecutionSummary {
  complete: boolean;
  results: Array<{
    batch_id: string;
    completed: number;
    failed: number;
    results: Array<{ sha256: string; status: string; reason?: string; error?: string; written?: boolean }>;
  }>;
  failures: Array<{ batch_id: string; error: string }>;
}

export interface ArchivePlanItem {
  batch_id: string;
  sha256: string;
  current_sha256: string;
  source_path: string | null;
  name: string;
  media_type: string;
  size: number;
  action: 'archive' | 'skip';
  relative_path: string | null;
  reason: string;
  record_kind: 'media' | 'sidecar';
  record_id: string | null;
  group_id: string | null;
  owner_sha256: string | null;
}

export interface ArchivePlanSummary {
  archive_root: string;
  plan_fingerprint: string;
  batch_ids: string[];
  items: ArchivePlanItem[];
  counts: Record<'archive' | 'already-archived' | 'quarantined' | 'skipped', number>;
  total_bytes: number;
}

export interface ArchiveExecutionSummary {
  complete: boolean;
  archive_root: string;
  batch_ids: string[];
  results: Array<{
    sha256: string;
    record_kind: 'media' | 'sidecar';
    status: 'archived' | 'failed' | 'skipped';
    copied?: boolean;
    reason?: string;
    error?: string;
  }>;
  archived: number;
  failed: number;
  skipped: number;
  snapshot_path: string | null;
  snapshot_error: string | null;
  source_cleanup: 'retained-pending-separate-approval';
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
