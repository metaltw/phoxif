# phoxif — UI/UX Design Spec

## Core Philosophy

- **Data safety is non-negotiable** — no code path for permanent deletion
- **Production-line model** — dry run first, mass execute second
- **Tool, not library** — open → process → close. Not a photo browser

## Safety Guarantees (Code-Level)

| Operation | Mechanism | Storage Impact |
|-----------|-----------|----------------|
| Remove files | `send2trash` only. `os.remove()` forbidden | Zero (system Trash) |
| EXIF write | Log old/new values. Undo = write back. No `_original` files | Zero |
| Video convert | Output new file. Original untouched until user manually trashes | Temporary increase |
| Rename | Log old→new mapping. Reversible | Zero |
| All operations | `.phoxif_log.json` with full undo support | Negligible |

### Hard Rules

- `os.remove()` / `os.unlink()` / `shutil.rmtree()` — **NEVER** used on user files
- `exiftool -overwrite_original` — **NEVER** used
- Video originals — **NEVER** auto-deleted

## Flow Model

```
┌─────────────┐
│   1. SCAN   │  Select folder → auto-scan → show summary
└──────┬──────┘
       ▼
┌─────────────┐
│  2. REVIEW  │  Summary cards → click into each category
│  (Dry Run)  │  Make selections, preview changes
│             │  No actual changes. Can pause/resume anytime.
└──────┬──────┘
       ▼
┌─────────────┐
│ 3. CONFIRM  │  One page listing ALL queued actions
│             │  Toggle on/off per category
│             │  Only reviewed categories shown
└──────┬──────┘
       ▼
┌─────────────┐
│ 4. EXECUTE  │  Progress bar. User can walk away.
│             │  No interaction needed.
└──────┬──────┘
       ▼
┌─────────────┐
│   5. DONE   │  Report + Undo all + log location
└─────────────┘
```

## Review Phase — Per Category Behavior

| Category | Review Style | Reason |
|----------|-------------|--------|
| Duplicates | Batch — auto-select best, user scans groups | MD5 identical, low risk |
| Similar Photos | Per-group — user picks favorites | Needs human judgment |
| Video Convert | Batch — list with estimated savings | Non-destructive (keeps originals) |
| Rename | Batch — preview table (old→new) | Rule-based, predictable |
| GPS Write | Per-folder — assign coordinates | Needs location confirmation |
| Orientation Fix | Per-photo — AI flags, user picks 0/90/180/270 | Needs human eye |
| Organize/Sort | Individual — drag to location | Manual classification |

## Summary Card States

```
[ ] Not reviewed — default, neutral
[→] In progress — user is reviewing
[✓] Reviewed — ready to execute
[—] Skipped — user chose to skip
[✓✓] Done — executed successfully
```

## UI Layout

### Scan Screen (Step 1)
- Centered drop zone
- Safety pledge banner (shield icon + guarantees)
- Recent folders list

### Summary Screen (Step 2)
- Grid of summary cards, one per category
- Each card shows: icon, title, count, impact description
- Card states: neutral / reviewed ✓ / no issues ✓
- Cards with zero findings shown as "OK" (dimmed)
- Click card → enter category detail view → back to summary

### Category Detail Views
- Back button → return to summary
- Category-specific content (groups / table / grid)
- Bulk action bar at top (e.g., "Auto: keep largest")
- Status line: "X files selected for Trash · Y MB"
- Preview panel: slide-in on thumbnail click, collapsed by default
- EXIF: 5 essential rows default, "Show all" expander

### Confirm Screen (Step 3)
- Centered card listing all queued actions
- Each action: icon + description + count + toggle switch
- "Show file list" expander per action
- Safety guarantees box
- Buttons: "Back to review" / "Execute N actions"

### Done Screen (Step 5)
- Summary of completed actions
- "Operation History" + "Undo this session" buttons
- Trash recovery instructions
- Log file location

### History Screen (from topbar or Done)
- Session-based operation log (like git log)
- Each session: date, summary, expandable detail list
- Per-session "Undo" button — reverts entire session
- Undone sessions shown with UNDONE label + dimmed + strikethrough

## Visual Design

- Dark theme (near-black background)
- Green for safe/keep/done states
- Amber (not red) for suggested removals during review
- Red only at final destructive confirmation (if ever needed)
- Accent blue for interactive elements
- Safety banners: dark green background + green border

## Tech Stack

- **Backend:** FastAPI (Python) — wraps existing phoxif modules as API
- **Frontend:** React — SPA with client-side routing
- **Communication:** REST API + WebSocket for progress updates
- **Launch:** `python -m phoxif` opens browser to `localhost:8899`

## Similar Photo Detection Strategy

1. Group by metadata: same date ±N seconds + same GPS / same folder
2. Within groups: pHash/dHash comparison
3. Avoids N² full-scan — only compares within small clusters

## Orientation Fix

- **Problem:** Handheld camera shooting landscape, EXIF orientation tag missing/wrong
- **Detection:** OpenCV face detection to infer expected orientation vs EXIF
- **Scope:** Only 0°/90°/180°/270° — no angle fine-tuning
- **Fix method:** Write EXIF Orientation tag only — no image re-encoding, zero quality loss
- **Review:** Per-photo, show thumbnail + 4 rotation options, AI pre-selects suggested
- **Undo:** Log old→new orientation value, write back to revert

## Undo / Operation History

- Every Execute session logged to `.phoxif_log.json`
- Log format: timestamp, operation type, file path, old value, new value
- Session-level undo: revert all operations in a session
- Trash undo: move files back from system Trash
- Rename undo: reverse old→new mapping
- GPS undo: write back old coordinates (or null)
- Orientation undo: write back old tag value
- Convert: delete new file (original was never touched)
