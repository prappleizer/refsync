#!/usr/bin/env python3
"""refsync-explore CLI: launch the explore server."""

import argparse
import os
import sys
import threading
import time
import webbrowser


def main():
    parser = argparse.ArgumentParser(
        prog="refsync-explore",
        description="refsync-explore: project-based literature search and triage on ADS",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8001, help="Port to bind to (default: 8001)")
    parser.add_argument(
        "--refsync-port",
        type=int,
        default=None,
        help="Port the refsync server runs on, for links (default: 8000)",
    )
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser")
    parser.add_argument("--reload", action="store_true", help="Auto-reload for development")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    args = parser.parse_args()

    if args.refsync_port:
        os.environ["REFSYNC_PORT"] = str(args.refsync_port)

    url = f"http://{args.host}:{args.port}"
    print(f"\n  refsync-explore  ->  {url}\n  Press Ctrl+C to stop.\n")

    if not args.no_browser:

        def _open():
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install 'uvicorn[standard]'")
        sys.exit(1)

    try:
        if args.reload:
            uvicorn.run("refsync_explore.main:app", host=args.host, port=args.port, reload=True)
        else:
            from refsync_explore.main import app

            uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
