"""Operation logger for phoxif — session-based undo support."""

import json
import os
import tempfile
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
        status: str = "completed",
    ) -> dict[str, Any]:
        """Append an operation to the current session.

        Args:
            op_type: Operation type (TRASH, RENAME, GPS, ORIENTATION, CONVERT).
            file: Absolute path to the affected file.
            old_value: Previous value (for undo).
            new_value: New value after operation.
            detail: Human-readable description.
            status: ``pending``, ``completed``, or ``failed``.

        Returns:
            Mutable operation record for write-ahead completion updates.
        """
        if self._current_session is None:
            self.start_session()

        assert self._current_session is not None
        operation = {
            "type": op_type,
            "file": file,
            "old_value": old_value,
            "new_value": new_value,
            "detail": detail,
            "status": status,
        }
        self._current_session["operations"].append(operation)
        return operation

    def mark_operation(
        self,
        operation: dict[str, Any],
        status: str,
        *,
        detail: str | None = None,
    ) -> None:
        """Persist a write-ahead operation status transition."""
        if status not in {"pending", "completed", "failed"}:
            raise ValueError(f"Invalid operation status: {status}")
        previous_status = operation.get("status")
        previous_detail = operation.get("detail")
        operation["status"] = status
        if detail is not None:
            operation["detail"] = detail
        try:
            self.save()
        except OSError:
            operation["status"] = previous_status
            operation["detail"] = previous_detail
            raise

    def save(self) -> None:
        """Atomically write the operation log to disk."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".phoxif-log-",
            suffix=".json",
            dir=self.log_path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w") as file_handle:
                json.dump(self.sessions, file_handle, indent=2, ensure_ascii=False)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temp_path, self.log_path)
        finally:
            temp_path.unlink(missing_ok=True)

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

        # Undo in reverse order, checkpointing each operation so partial success
        # can be retried without reapplying already completed work.
        for op in reversed(session["operations"]):
            if op.get("undo_status") == "completed":
                result = {
                    "op": op,
                    "success": True,
                    "detail": op.get("undo_detail", "Already restored"),
                }
                results.append(result)
                continue

            op["undo_status"] = "pending"
            op["undo_attempted_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
            result = self._undo_operation(op)
            op["undo_status"] = "completed" if result["success"] else "failed"
            op["undo_detail"] = result["detail"]
            if result["success"]:
                op["undone_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
            results.append(result)

        all_succeeded = all(result["success"] for result in results)
        session["undone"] = all_succeeded
        timestamp_key = "undone_at" if all_succeeded else "undo_attempted_at"
        session[timestamp_key] = datetime.now(timezone.utc).isoformat()
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

        if op.get("status", "completed") == "failed":
            return {
                "op": op,
                "success": True,
                "detail": "Operation was not applied; nothing to undo",
            }

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
                if old_path.exists():
                    return {
                        "op": op,
                        "success": True,
                        "detail": f"Already renamed back: {old_path.name}",
                    }
                else:
                    return {
                        "op": op,
                        "success": False,
                        "detail": f"File not found: {new_path}",
                    }

            elif op_type == "GPS":
                from phoxif.api.exif_writer import write_tags

                # Write back old GPS value via exiftool
                old_val = op["old_value"]
                if old_val:
                    # old_value format: "lat,lon"
                    lat, lon = old_val.split(",")
                    latitude = float(lat.strip())
                    longitude = float(lon.strip())
                    tags = {
                        "GPSLatitude": latitude,
                        "GPSLatitudeRef": "S" if latitude < 0 else "N",
                        "GPSLongitude": longitude,
                        "GPSLongitudeRef": "W" if longitude < 0 else "E",
                    }
                else:
                    # Remove GPS tags
                    tags = {
                        "GPSLatitude": "",
                        "GPSLongitude": "",
                        "GPSLatitudeRef": "",
                        "GPSLongitudeRef": "",
                    }
                write_tags(Path(file_path), tags, numeric=True)
                return {
                    "op": op,
                    "success": True,
                    "detail": f"GPS restored for {Path(file_path).name}",
                }

            elif op_type == "ORIENTATION":
                from phoxif.api.exif_writer import write_tags

                old_val = op["old_value"]
                if old_val:
                    write_tags(
                        Path(file_path),
                        {"Orientation": int(old_val)},
                        numeric=True,
                    )
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
                    "success": True,
                    "detail": "Converted file is already absent",
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
