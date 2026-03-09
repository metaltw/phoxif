"""Local web UI for previewing and sorting Unknown photos/videos by location.

Serves a browser-based interface to manually classify files that could not
be automatically organized by GPS metadata.

Usage:
    python -m phoxif.sorter --config config.yaml [--port 8899]
"""

import argparse
import json
import subprocess
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

from phoxif.config import load_config

# Module-level state set by main() before server starts
_base_dir: Path
_unknown_dir: Path
_thumb_dir: Path
_preview_dir: Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
HEIC_EXTS = {".heic"}
VIDEO_EXTS = {".mov", ".mp4"}


def get_existing_locations(base_dir: Path) -> list[str]:
    """Get sorted list of existing location folder names.

    Args:
        base_dir: Base directory containing location folders.

    Returns:
        Sorted list of folder names, excluding system directories.
    """
    skip = {"Unknown", ".thumbnails", ".previews", "__pycache__"}
    return sorted(
        [
            d.name
            for d in base_dir.iterdir()
            if d.is_dir() and d.name not in skip and not d.name.startswith(".")
        ]
    )


def get_unknown_files(unknown_dir: Path) -> list[dict[str, str]]:
    """Get list of unclassified media files.

    Args:
        unknown_dir: Path to the Unknown folder.

    Returns:
        List of dicts with keys: name, type, ext.
    """
    if not unknown_dir.exists():
        return []
    files: list[dict[str, str]] = []
    for f in sorted(unknown_dir.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.stat().st_size == 0:
            continue
        ext = f.suffix.lower()
        if ext in IMAGE_EXTS:
            ftype = "image"
        elif ext in HEIC_EXTS:
            ftype = "heic"
        elif ext in VIDEO_EXTS:
            ftype = "video"
        else:
            continue
        files.append({"name": f.name, "type": ftype, "ext": ext})
    return files


def ensure_thumbnail(filename: str, unknown_dir: Path, thumb_dir: Path) -> str | None:
    """Ensure thumbnail exists for HEIC files, return thumb filename.

    Args:
        filename: Source filename in Unknown folder.
        unknown_dir: Path to the Unknown folder.
        thumb_dir: Path to the thumbnails directory.

    Returns:
        Thumbnail filename if successful, None otherwise.
    """
    thumb_name = Path(filename).stem + ".jpg"
    thumb_path = thumb_dir / thumb_name
    if thumb_path.exists():
        return thumb_name
    src = unknown_dir / filename
    if not src.exists():
        return None
    thumb_dir.mkdir(exist_ok=True)
    try:
        subprocess.run(
            ["sips", "-Z", "600", str(src), "--out", str(thumb_path)],
            capture_output=True,
            timeout=10,
        )
        if thumb_path.exists():
            return thumb_name
    except Exception:
        pass
    return None


def build_html(
    base_dir: Path, unknown_dir: Path, thumb_dir: Path, preview_dir: Path
) -> str:
    """Build the full HTML page for the sorter UI.

    Args:
        base_dir: Base directory containing location folders.
        unknown_dir: Path to the Unknown folder.
        thumb_dir: Path to the thumbnails directory.
        preview_dir: Path to video previews directory.

    Returns:
        Complete HTML page as string.
    """
    files = get_unknown_files(unknown_dir)
    locations = get_existing_locations(base_dir)
    options_html = "\n".join(f'<option value="{loc}">' for loc in locations)

    file_cards: list[str] = []
    for i, f in enumerate(files):
        encoded_name = urllib.parse.quote(f["name"])
        if f["type"] == "image":
            media = (
                f'<img src="/files/{encoded_name}" alt="{f["name"]}" loading="lazy">'
            )
        elif f["type"] == "heic":
            thumb = ensure_thumbnail(f["name"], unknown_dir, thumb_dir)
            if thumb:
                media = f'<img src="/thumbnails/{urllib.parse.quote(thumb)}" alt="{f["name"]}" loading="lazy">'
            else:
                media = '<div class="no-preview">HEIC - No preview</div>'
        else:
            preview_name = Path(f["name"]).stem + ".mp4"
            preview_path = preview_dir / preview_name
            if preview_path.exists():
                video_src = f"/previews/{urllib.parse.quote(preview_name)}"
            else:
                video_src = f"/files/{encoded_name}"
            media = f'''<video controls preload="metadata" muted playsinline>
                <source src="{video_src}" type="video/mp4">
            </video>'''

        file_cards.append(f'''
        <div class="card" id="card-{i}" data-filename="{f["name"]}">
            <div class="media">{media}</div>
            <div class="info">
                <span class="filename">{f["name"]}</span>
                <div class="actions">
                    <input type="text" list="locations" placeholder="Location..."
                           class="loc-input" id="loc-{i}" autocomplete="off">
                    <button onclick="doAssign({i})" class="btn-assign">Set</button>
                    <button onclick="doDelete({i})" class="btn-delete">Del</button>
                    <button onclick="doSkip({i})" class="btn-skip">Skip</button>
                </div>
            </div>
        </div>''')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>phoxif - Photo Sorter</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a2e; color: #eee; padding: 20px; }}
h1 {{ text-align: center; margin-bottom: 10px; color: #e94560; }}
.stats {{ text-align: center; margin-bottom: 20px; color: #999; font-size: 15px; }}
.container {{ max-width: 900px; margin: 0 auto; }}
.card {{ background: #16213e; border-radius: 12px; margin-bottom: 16px; overflow: hidden; transition: opacity 0.3s; }}
.card.done {{ opacity: 0.3; }}
.card.deleted {{ opacity: 0.15; border: 2px solid #e94560; }}
.media {{ width: 100%; max-height: 500px; display: flex; justify-content: center; background: #0f0f23; }}
.media img, .media video {{ max-width: 100%; max-height: 500px; object-fit: contain; }}
.no-preview {{ padding: 40px; color: #666; text-align: center; }}
.info {{ padding: 12px 16px; }}
.filename {{ font-size: 14px; color: #999; font-family: monospace; }}
.actions {{ display: flex; gap: 8px; margin-top: 8px; align-items: center; }}
.loc-input {{ flex: 1; padding: 8px 12px; border: 1px solid #333; border-radius: 6px; background: #0f0f23; color: #eee; font-size: 15px; }}
.loc-input:focus {{ border-color: #e94560; outline: none; }}
.btn-assign {{ padding: 8px 16px; background: #533483; color: white; border: none; border-radius: 6px; cursor: pointer; }}
.btn-assign:hover {{ background: #6a42a0; }}
.btn-delete {{ padding: 8px 12px; background: #e94560; color: white; border: none; border-radius: 6px; cursor: pointer; }}
.btn-delete:hover {{ background: #c73652; }}
.btn-skip {{ padding: 8px 12px; background: #333; color: #999; border: none; border-radius: 6px; cursor: pointer; }}
.btn-skip:hover {{ background: #444; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 8px; }}
.badge-set {{ background: #533483; }}
.badge-del {{ background: #e94560; }}
.status {{ position: fixed; top: 10px; right: 10px; padding: 6px 12px; border-radius: 6px; font-size: 13px; z-index: 999; transition: opacity 0.5s; }}
.status-ok {{ background: #2d6a4f; color: white; }}
.status-err {{ background: #e94560; color: white; }}
</style>
</head>
<body>
<h1>phoxif - Photo Sorter</h1>
<p class="stats">{len(files)} files | <span id="progress">0</span> assigned | <span id="del-count">0</span> deleted</p>
<div id="status-msg" class="status" style="opacity:0"></div>
<datalist id="locations">
{options_html}
</datalist>
<div class="container">
{"".join(file_cards)}
</div>
<script>
let assignCount = 0;
let delCount = 0;

function showStatus(msg, ok) {{
    const el = document.getElementById("status-msg");
    el.textContent = msg;
    el.className = "status " + (ok ? "status-ok" : "status-err");
    el.style.opacity = 1;
    setTimeout(() => el.style.opacity = 0, 2000);
}}

function updateStats() {{
    document.getElementById("progress").textContent = assignCount;
    document.getElementById("del-count").textContent = delCount;
}}

function sendAction(action, filename, location) {{
    return fetch("/api/action", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ action, filename, location }}),
    }})
    .then(r => r.json())
    .then(d => {{
        if (d.ok) showStatus("Done: " + d.msg, true);
        else showStatus("Error: " + d.msg, false);
        return d;
    }})
    .catch(e => {{
        showStatus("Connection error - retrying...", false);
        return new Promise(resolve => setTimeout(() => {{
            fetch("/api/action", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ action, filename, location }}),
            }})
            .then(r => r.json())
            .then(d => {{ showStatus("Done: " + d.msg, true); resolve(d); }})
            .catch(() => {{ showStatus("Failed", false); resolve({{ok:false}}); }});
        }}, 2000));
    }});
}}

function doAssign(i) {{
    const input = document.getElementById("loc-" + i);
    const val = input.value.trim();
    if (!val) {{ alert("Please enter a location"); return; }}
    const card = document.getElementById("card-" + i);
    const filename = card.dataset.filename;

    sendAction("move", filename, val).then(d => {{
        if (!d.ok) return;
        card.classList.remove("deleted");
        card.classList.add("done");
        const datalist = document.getElementById("locations");
        const existing = Array.from(datalist.options).map(o => o.value);
        if (!existing.includes(val)) {{
            const opt = document.createElement("option");
            opt.value = val;
            datalist.appendChild(opt);
        }}
        let badge = card.querySelector(".badge");
        if (!badge) {{
            badge = document.createElement("span");
            card.querySelector(".info").appendChild(badge);
        }}
        badge.className = "badge badge-set";
        badge.textContent = val;
        assignCount++;
        updateStats();
        const next = card.nextElementSibling;
        if (next && !next.classList.contains("done") && !next.classList.contains("deleted"))
            next.scrollIntoView({{ behavior: "smooth", block: "center" }});
    }});
}}

function doDelete(i) {{
    const card = document.getElementById("card-" + i);
    const filename = card.dataset.filename;
    if (card.classList.contains("deleted")) return;

    sendAction("delete", filename, "").then(d => {{
        if (!d.ok) return;
        card.classList.add("deleted");
        let badge = card.querySelector(".badge");
        if (!badge) {{
            badge = document.createElement("span");
            card.querySelector(".info").appendChild(badge);
        }}
        badge.className = "badge badge-del";
        badge.textContent = "DELETED";
        delCount++;
        updateStats();
    }});
}}

function doSkip(i) {{
    const card = document.getElementById("card-" + i);
    let next = card.nextElementSibling;
    while (next && (next.classList.contains("done") || next.classList.contains("deleted")))
        next = next.nextElementSibling;
    if (next) next.scrollIntoView({{ behavior: "smooth", block: "center" }});
}}

document.querySelectorAll(".loc-input").forEach((input, i) => {{
    input.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") doAssign(i);
    }});
}});
</script>
</body>
</html>"""


class SorterHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the photo sorter web UI."""

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            html = build_html(_base_dir, _unknown_dir, _thumb_dir, _preview_dir)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
        elif self.path.startswith("/files/"):
            self._serve_file(_unknown_dir, self.path[7:])
        elif self.path.startswith("/previews/"):
            self._serve_file(_preview_dir, self.path[10:], content_type="video/mp4")
        elif self.path.startswith("/thumbnails/"):
            self._serve_file(_thumb_dir, self.path[12:], content_type="image/jpeg")
        else:
            self.send_error(404)

    def _serve_file(
        self, directory: Path, encoded_name: str, content_type: str | None = None
    ) -> None:
        """Serve a file from the given directory."""
        filename = urllib.parse.unquote(encoded_name)
        filepath = directory / filename
        if not filepath.exists():
            self.send_error(404)
            return
        if content_type is None:
            ext = filepath.suffix.lower()
            content_types = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".mov": "video/mp4",
                ".mp4": "video/mp4",
            }
            content_type = content_types.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(filepath.stat().st_size))
        self.end_headers()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def do_POST(self) -> None:
        if self.path == "/api/action":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            action = body.get("action")
            filename = body.get("filename", "")
            location = body.get("location", "")

            try:
                if action == "move":
                    src = _unknown_dir / filename
                    if not src.exists():
                        self._json({"ok": False, "msg": f"File not found: {filename}"})
                        return
                    dst_dir = _base_dir / location
                    dst_dir.mkdir(exist_ok=True)
                    dst = dst_dir / filename
                    n = 1
                    while dst.exists():
                        dst = dst_dir / f"{src.stem}_{n}{src.suffix}"
                        n += 1
                    src.rename(dst)
                    self._json({"ok": True, "msg": f"{filename} -> {location}"})

                elif action == "delete":
                    src = _unknown_dir / filename
                    if src.exists():
                        src.unlink()
                    self._json({"ok": True, "msg": f"Deleted {filename}"})

                else:
                    self._json({"ok": False, "msg": f"Unknown action: {action}"})
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
        else:
            self.send_error(404)

    def _json(self, data: dict[str, Any]) -> None:
        """Send a JSON response."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


def main(argv: list[str] | None = None) -> None:
    """Entry point for the photo sorter web UI."""
    global _base_dir, _unknown_dir, _thumb_dir, _preview_dir

    parser = argparse.ArgumentParser(
        description="Web UI for sorting Unknown photos/videos."
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument(
        "--base-dir", default=None, help="Override base_dir from config"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to listen on (overrides config)"
    )
    args = parser.parse_args(argv)

    cfg = load_config(args.config, base_dir_override=args.base_dir)
    _base_dir = cfg["base_dir"]
    _unknown_dir = _base_dir / "Unknown"
    _thumb_dir = _base_dir / ".thumbnails"
    _preview_dir = _base_dir / ".previews"

    port = args.port or cfg["sorter_port"]

    print(f"Starting Photo Sorter on http://localhost:{port}")
    print(
        f"Files: {len(get_unknown_files(_unknown_dir))} | Locations: {len(get_existing_locations(_base_dir))}"
    )
    print("Press Ctrl+C to stop")
    server = HTTPServer(("127.0.0.1", port), SorterHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
