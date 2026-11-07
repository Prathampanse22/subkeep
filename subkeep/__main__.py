"""Start SubKeep:  python -m subkeep"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .db import Store, default_data_dir
from .server import make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="subkeep", description="Private subscription & expense tracker")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    parser.add_argument("--data-dir", type=Path, default=None, help="folder for the database file")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--version", action="version", version=f"SubKeep {__version__}")
    args = parser.parse_args(argv)

    data_dir = (args.data_dir or default_data_dir()).expanduser()
    db_path = data_dir / "subkeep.db"
    store = Store(db_path)

    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = make_server(store, port=port)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not find a free port near {args.port}. Try --port 9000.", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"SubKeep {__version__} is running at {url}")
    print(f"Your data is saved in: {db_path}")
    print("Keep this window open while you use SubKeep. Press Ctrl+C to stop.")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping SubKeep. Bye!")
    finally:
        httpd.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
