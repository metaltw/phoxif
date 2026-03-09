import type { ScanResult, Session, FileInfo, SimilarGroup, OrientationIssue, RenamePreview } from './types';

const BASE_URL = '/api';

interface ApiResponse<T> {
  ok: boolean;
  data: T;
  error: string | null;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    throw new Error(`HTTP error: ${res.status} ${res.statusText}`);
  }
  const body = await res.json() as ApiResponse<T>;
  if (!body.ok) {
    throw new Error(body.error || 'Unknown API error');
  }
  return body.data;
}

interface ScanFileData {
  path: string;
  filename: string;
  extension: string;
  size: number;
  date: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  orientation: number | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  directory: string;
  mime_type: string;
}

function mapFile(f: ScanFileData): FileInfo {
  return {
    path: f.path,
    name: f.filename,
    size: f.size || 0,
    date: f.date || null,
    gps: (f.gps_lat != null && f.gps_lon != null)
      ? { lat: f.gps_lat, lon: f.gps_lon }
      : null,
    orientation: f.orientation || null,
    codec: f.codec || null,
    extension: f.extension || (f.filename ? f.filename.split('.').pop() ?? '' : ''),
    width: f.width || null,
    height: f.height || null,
  };
}

interface ScanData {
  files: ScanFileData[];
  stats: {
    total_files: number;
    total_size: number;
    photo_count: number;
    video_count: number;
    with_gps: number;
    without_gps: number;
  };
  duplicates: Array<{
    hash: string;
    count: number;
    wasted_size: number;
    files: ScanFileData[];
  }>;
  similar_groups?: Array<{
    files: ScanFileData[];
    count: number;
    keep_index: number;
    similarities: Array<{ i: number; j: number; distance: number; similarity: number }>;
    reason: 'burst' | 'similar';
    reclaimable_size: number;
  }>;
  orientation_issues?: Array<{
    file: ScanFileData;
    current_orientation: number;
    label: string;
  }>;
  rename_preview?: Array<{
    file: ScanFileData;
    old_name: string;
    new_name: string;
    new_path: string;
  }>;
  exiftool_available: boolean;
  duplicate_stats: {
    groups: number;
    total_duplicates: number;
    wasted_size: number;
  };
}

export async function scanFolder(path: string): Promise<ScanResult> {
  const data = await request<ScanData>('/scan', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });

  const duplicates = data.duplicates.map((group, idx) => ({
    id: idx + 1,
    reason: 'MD5 match',
    keep_index: 0,
    files: group.files.map(mapFile),
  }));

  const similar_groups: SimilarGroup[] = (data.similar_groups ?? []).map((g, idx) => ({
    id: idx + 1,
    files: g.files.map(mapFile),
    keep_index: g.keep_index,
    similarities: g.similarities,
    reason: g.reason,
    reclaimable_size: g.reclaimable_size,
  }));

  const orientation_issues: OrientationIssue[] = (data.orientation_issues ?? []).map(o => ({
    file: mapFile(o.file),
    current_orientation: o.current_orientation,
    label: o.label,
  }));

  const rename_preview: RenamePreview[] = (data.rename_preview ?? []).map(r => ({
    file: mapFile(r.file),
    old_name: r.old_name,
    new_name: r.new_name,
    new_path: r.new_path,
  }));

  return {
    total_files: data.stats.total_files,
    total_size: data.stats.total_size,
    base_dir: path,
    duplicates,
    similar_groups,
    orientation_issues,
    rename_preview,
  };
}

export async function trashFiles(files: string[]): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/duplicates/trash', {
    method: 'POST',
    body: JSON.stringify({ files }),
  });
}

export async function fixOrientation(files: Array<{ path: string; orientation: number }>): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/orientation/fix', {
    method: 'POST',
    body: JSON.stringify({ files }),
  });
}

export async function executeRenames(renames: Array<{ old: string; new: string }>): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/rename/execute', {
    method: 'POST',
    body: JSON.stringify({ renames }),
  });
}

export async function pickFolder(): Promise<string | null> {
  try {
    const data = await request<{ path: string }>('/pick-folder');
    return data.path;
  } catch {
    return null;
  }
}

export async function revealInFinder(path: string): Promise<void> {
  await fetch(`${BASE_URL}/reveal?path=${encodeURIComponent(path)}`);
}

export async function getHistory(): Promise<Session[]> {
  return request<Session[]>('/history');
}

export async function undoSession(index: number): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/history/undo', {
    method: 'POST',
    body: JSON.stringify({ session_index: index }),
  });
}
