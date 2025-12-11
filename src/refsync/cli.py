#!/usr/bin/env python3
"""
RefSync CLI - Launch the RefSync server.
"""

import argparse
import sys
import threading
import time
import webbrowser


def open_browser(url: str, delay: float = 1.5):
    """Open browser after a short delay to let server start."""
    time.sleep(delay)
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(
        prog="refsync",
        description="RefSync - A citation manager for astronomers with ADS sync",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )

    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"

    # Print startup message
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║   ██████╗ ███████╗███████╗███████╗██╗   ██╗███╗   ██╗ ██████╗  ║
║   ██╔══██╗██╔════╝██╔════╝██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝  ║
║   ██████╔╝█████╗  █████╗  ███████╗ ╚████╔╝ ██╔██╗ ██║██║       ║
║   ██╔══██╗██╔══╝  ██╔══╝  ╚════██║  ╚██╔╝  ██║╚██╗██║██║       ║
║   ██║  ██║███████╗██║     ███████║   ██║   ██║ ╚████║╚██████╗  ║
║   ╚═╝  ╚═╝╚══════╝╚═╝     ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝  ║
║                                                           ║
║   Citation manager for astronomers with ADS sync          ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝

Starting server at {url}
Press Ctrl+C to stop.
""")

    # Open browser in background thread
    if not args.no_browser:
        browser_thread = threading.Thread(target=open_browser, args=(url,), daemon=True)
        browser_thread.start()

    # Import uvicorn here to avoid slow startup for --help
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    # Run the server
    try:
        if args.reload:
            # Reload mode requires string import path
            uvicorn.run(
                "refsync.main:app",
                host=args.host,
                port=args.port,
                reload=True,
                log_level="info",
            )
        else:
            # Direct import is more reliable for installed packages
            from refsync.main import app

            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                log_level="info",
            )
    except KeyboardInterrupt:
        print("\nShutting down RefSync...")
        sys.exit(0)


if __name__ == "__main__":
    main()
