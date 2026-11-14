"""Entry point: the app window by default, or a command line with --cli."""

from __future__ import annotations

import argparse
import sys

from . import __version__, log
from .app import AppPaths, Runner, startup, summary
from .log import UserError
from .restore import restore_files
from .system import SingleInstance


def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="ImageCleanuper", description="ImageCleanuper command line")
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("command", choices=["run", "status", "restore-all"])
    args = ap.parse_args(argv)
    paths = AppPaths.default()
    log.setup_logging(paths.data, console=True)
    st = startup(paths)
    if st.catalog_damaged:
        print("The catalog is damaged. Start the app window to restore it from a learned-data backup.")
        return 2
    for p in st.problems:
        print("Settings:", p)
    if args.command == "status":
        print(summary(paths))
        return 0
    if args.command == "run":
        def progress(label, done, total):
            print(f"\r{label}: {done}/{total or '?'}", end="", flush=True)
        try:
            res = Runner(paths, st.settings, progress).run()
        except UserError as e:
            print("\n" + str(e))
            return 1
        print(f"\nRun {res.run_id}: {res.state}. {res.message}")
        return 0
    if args.command == "restore-all":
        from .catalog import Catalog
        cat = Catalog(paths.catalog)
        r = restore_files(cat, st.settings.backup)
        cat.close()
        print(r)
        return 0
    return 1


def main() -> int:
    # A windowed .exe has no console: sys.stdout/stderr are None and print() would crash.
    import os
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    argv = sys.argv[1:]
    if "--version" in argv:
        print(__version__)
        return 0
    if "--selftest" in argv:
        from .selftest import run as selftest
        return selftest()
    lock = SingleInstance()
    if not lock.acquire():
        print("ImageCleanuper is already running.")
        return 3
    try:
        if "--cli" in argv:
            return _cli(argv)
        from .ui.window import run_window
        return run_window()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
