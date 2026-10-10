#!/usr/bin/env python3
"""Fetch (and, where needed, compile) the pinned akaiutil release into build/bin/.

    uv run python scripts/get_akaiutil.py

You rarely need this: akaiutil-gui offers to do the same the first time it can't find
akaiutil. It's here for scripting, CI, and building ahead of time. The work is done by
akaiutil_gui.engine.fetch (download over HTTPS, SHA-256 check, compile).

    --build      on Windows, compile from source (MinGW gcc) instead of using the prebuilt exe
    --dest DIR   install somewhere other than build/bin
    --source-only DIR   don't build anything: just download the pinned source tarball into DIR
                        (used by the release workflow to attach it next to the built apps)
"""

import argparse
import sys

from akaiutil_gui.engine import fetch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", action="store_true", help="on Windows, compile instead of using the prebuilt exe")
    ap.add_argument("--dest", help="install directory (default: build/bin)")
    ap.add_argument("--source-only", metavar="DIR", help="only download the verified source tarball into DIR")
    args = ap.parse_args()
    try:
        if args.source_only:
            print(f"\nsaved {fetch.download_source(args.source_only, progress=print)}")
            return 0
        path = fetch.install(args.dest, build_from_source=args.build or None, progress=print)
    except fetch.FetchError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    print(f"\nakaiutil {fetch.VERSION} ready: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
