"""Operation logger for phoxif — session-based undo support."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class OperationLogger:
    """Logs all file operations to a JSON file for undo support.

    Log file structure:
        [
            {
                "session_id": 0,
                "timestamp": "2024-01-01T00:00:00Z",
                "operations": [
                    {
                        "type": "TRASH",
                        "file": "/path/to/file.jpg",
                        "old_value": null,
                        "new_value": null,
                        "detail": "sent to trash"
                    }
                ],
                "undone": false
            }
        ]
    """

    def __init__(self, base_dir: Path) -> None:
        """Initialize logger for a base directory.

        Args:
            base_dir: Directory where .phoxif_log.json will be stored.
        """
        self.base_dir = base_dir
        self.log_path = base_dir / ".phoxif_log.json"
        self.sessions: list[dict[str, Any]] = []
        self._current_session: dict[str, Any] | None = None
        self._load()

    def _load(self) -> None:
        """Load existing log from disk."""
        if self.log_path.exists():
            try:
                with open(self.log_path) as f:
                    self.sessions = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.sessions = []
        else:
            self.sessions = []

    def start_session(self) -> int:
        """Create a new session entry.

        Returns:
            The index of the new session.
        """
        session: dict[str, Any] = {
            "session_id": len(self.sessions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operations": [],
            "undone": False,
        }
        self.sessions.append(session)
        self._current_session = session
        return session["session_id"]

    def log_operation(
        self,
        op_type: str,
        file: str,
        old_value: str | None = None,
        new_value: str | None = None,
        detail: str = "",
    ) -> None:
        """Append an operation to the current session.

        Args:
            op_type: Operation type (TRASH, RENAME, GPS, ORIENTATION, CONVERT).
            file: Absolute path to the affected file.
            old_value: Previous value (for undo).
            new_value: New value after operation.
            detail: Human-readable description.
        """
        if self._current_session is None:
            self.start_session()

        assert self._current_session is not None
        self._current_session["operations"].append(
            {
                "type": op_type,
                "file": file,
                "old_value": old_value,
                "new_value": new_value,
                "detail": detail,
            }
        )

    def save(self) -> None:
        """Write log to disk."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "w") as f:
            json.dump(self.sessions, f, indent=2, ensure_ascii=False)

    def get_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions.

        Returns:
            List of session dicts.
        """
        return self.sessions

    def undo_session(self, session_index: int) -> list[dict[str, Any]]:
        """Reverse all operations in a session.

        Args:
            session_index: Index of the session to undo.

        Returns:
            List of undo results, each with {op, success, detail}.

        Raises:
            IndexError: If session_index is out of range.
            ValueError: If session was already undone.
        """
        if session_index < 0 or session_index >= len(self.sessions):
            raise IndexError(f"Session index {session_index} out of range")

        session = self.sessions[session_index]
        if session.get("undone"):
            raise ValueError(f"Session {session_index} was already undone")

        results: list[dict[str, Any]] = []

        # Undo in reverse order
        for op in reversed(session["operations"]):
            result = self._undo_operation(op)
            results.append(result)

        session["undone"] = True
        session["undone_at"] = datetime.now(timezone.utc).isoformat()
        self.save()
        return results

    def _undo_operation(self, op: dict[str, Any]) -> dict[str, Any]:
        """Undo a single operation.

        Args:
            op: Operation dict from the log.

        Returns:
            Result dict with {op, success, detail}.
        """
        op_type = op["type"]
        file_path = op["file"]

        try:
            if op_type == "TRASH":
                # Best effort — platform-dependent trash recovery
                return {
                    "op": op,
                    "success": False,
                    "detail": "Trash recovery requires manual action. "
                    "Check your system Trash for the file.",
                }

            elif op_type == "RENAME":
                old_path = Path(op["old_value"]) if op["old_value"] else Path(file_path)
                new_path = Path(op["new_value"]) if op["new_value"] else Path(file_path)
                # Reverse: rename new_value back to old_value
                if new_path.exists():
                    new_path.rename(old_path)
                    return {
                        "op": op,
                        "success": True,
                        "detail": f"Renamed back: {new_path.name} → {old_path.name}",
                    }
                else:
                    return {
                        "op": op,
                        "success": False,
                        "detail": f"File not found: {new_path}",
                    }

            elif op_type == "GPS":
                # Write back old GPS value via exiftool
                old_val = op["old_value"]
                if old_val:
                    # old_value format: "lat,lon"
                    lat, lon = old_val.split(",")
                    cmd = [
                        "exiftool",
                        f"-GPSLatitude={lat.strip()}",
                        f"-GPSLongitude={lon.strip()}",
                        file_path,
                    ]
                else:
                    # Remove GPS tags
                    cmd = [
                        "exiftool",
                        "-GPSLatitude=",
                        "-GPSLongitude=",
                        "-GPSLatitudeRef=",
                        "-GPSLongitudeRef=",
                        file_path,
                    ]
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                return {
                    "op": op,
                    "success": True,
                    "detail": f"GPS restored for {Path(file_path).name}",
                }

            elif op_type == "ORIENTATION":
                old_val = op["old_value"]
                if old_val:
                    cmd = [
                        "exiftool",
                        f"-Orientation={old_val}",
                        "-n",
                        file_path,
                    ]
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    return {
                        "op": op,
                        "success": True,
                        "detail": f"Orientation restored for {Path(file_path).name}",
                    }
                return {
                    "op": op,
                    "success": False,
                    "detail": "No old orientation value to restore",
                }

            elif op_type == "DATE_FIX":
                import os
                from datetime import datetime

                old_date_str = op["old_value"]
                if old_date_str:
                    old_dt = datetime.fromisoformat(old_date_str)
                    old_ts = old_dt.timestamp()
                    target = Path(file_path)
                    if target.exists():
                        stat = target.stat()
                        os.utime(target, (stat.st_atime, old_ts))
                        return {
                            "op": op,
                            "success": True,
                            "detail": f"Date restored for {target.name}",
                        }
                    return {
                        "op": op,
                        "success": False,
                        "detail": f"File not found: {file_path}",
                    }
                return {
                    "op": op,
                    "success": False,
                    "detail": "No old date value to restore",
                }

            elif op_type == "CONVERT":
                # Delete the converted file (original was never touched)
                converted = Path(op["new_value"]) if op["new_value"] else None
                if converted and converted.exists():
                    from send2trash import send2trash

                    send2trash(str(converted))
                    return {
                        "op": op,
                        "success": True,
                        "detail": f"Trashed converted file: {converted.name}",
                    }
                return {
                    "op": op,
                    "success": False,
                    "detail": "Converted file not found",
                }

            else:
                return {
                    "op": op,
                    "success": False,
                    "detail": f"Unknown operation type: {op_type}",
                }

        except Exception as e:
            return {
                "op": op,
                "success": False,
                "detail": f"Undo failed: {e}",
            }
