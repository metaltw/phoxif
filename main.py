"""phoxif — Photo/Video EXIF Metadata Toolkit.

Launch the web UI or run in server-only mode.
"""

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn


def main() -> None:
    """Entry point for phoxif."""
    parser = argparse.ArgumentParser(
        description="phoxif — Photo/Video EXIF Metadata Toolkit"
    )
    parser.add_argument(
        "--port", type=int, default=8899, help="Port to run the server on"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Don't open native window, use browser instead",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Development mode (CORS enabled, no static files)",
    )
    args = parser.parse_args()

    if not args.no_window and not args.dev:
        _launch_with_webview(args.port, args.dev)
    else:
        _launch_server_only(args.port, args.no_browser, args.dev)


def _launch_server_only(port: int, no_browser: bool, dev: bool) -> None:
    """Launch FastAPI server and optionally open browser."""
    if not no_browser:
        # Open browser after short delay to let server start
        timer = threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}"))
        timer.daemon = True
        timer.start()

    uvicorn.run(
        "phoxif.api.app:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


def _launch_with_webview(port: int, dev: bool) -> None:
    """Launch with native window using pywebview."""
    try:
        import webview
    except ImportError:
        print("pywebview not available, falling back to browser mode")
        _launch_server_only(port, no_browser=False, dev=dev)
        return

    # Start server in background thread
    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={
            "app": "phoxif.api.app:app",
            "host": "127.0.0.1",
            "port": port,
            "log_level": "warning",
        },
        daemon=True,
    )
    server_thread.start()

    # Give server a moment to start
    import time
    time.sleep(1)

    # Open native window
    webview.create_window(
        "phoxif",
        f"http://localhost:{port}",
        width=1280,
        height=820,
        min_size=(960, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
