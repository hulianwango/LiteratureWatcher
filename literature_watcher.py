from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app_paths import change_to_config_dir, resolve_config_path, setup_utf8_console


@contextmanager
def temporary_argv(argv: list[str]) -> Iterator[None]:
    previous = sys.argv[:]
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = previous


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LiteratureWatcher search, export, or settings editor.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override the configured search window.")
    parser.add_argument("--date", default=None, help="Report date in YYYY-MM-DD format.")
    parser.add_argument("--no-translate", action="store_true", help="Skip Tencent Cloud translation during export.")
    parser.add_argument("--search-only", action="store_true", help="Run search_literature.py only.")
    parser.add_argument("--export-only", action="store_true", help="Run export_report.py only.")
    parser.add_argument("--edit-config", action="store_true", help="Open the desktop search settings editor.")
    parser.add_argument("--no-pause", action="store_true", help="Do not wait for Enter before closing.")
    return parser.parse_args()


def run_search(config_path: Path, args: argparse.Namespace) -> None:
    import search_literature

    argv = ["search_literature.py", "--config", str(config_path)]
    if args.lookback_days is not None:
        argv.extend(["--lookback-days", str(args.lookback_days)])
    if args.date:
        argv.extend(["--date", args.date])

    with temporary_argv(argv):
        search_literature.main()


def run_export(config_path: Path, args: argparse.Namespace) -> None:
    import export_report

    argv = ["export_report.py", "--config", str(config_path)]
    if args.date:
        argv.extend(["--date", args.date])
    if args.no_translate:
        argv.append("--no-translate")

    with temporary_argv(argv):
        export_report.main()


def open_config_editor(config_path: Path) -> None:
    from config_editor import ConfigEditor

    app = ConfigEditor(config_path)
    app.mainloop()


def should_open_editor_by_default() -> bool:
    return bool(getattr(sys, "frozen", False) and len(sys.argv) == 1)


def main() -> int:
    setup_utf8_console()
    args = parse_args()
    config_path = resolve_config_path(args.config)
    change_to_config_dir(config_path)

    if not config_path.exists():
        print(f"[ERROR] Cannot find config file: {config_path}")
        return 2

    if args.edit_config or should_open_editor_by_default():
        open_config_editor(config_path)
        return 0

    if args.search_only and args.export_only:
        print("[ERROR] --search-only and --export-only cannot be used together.")
        return 2

    if not args.export_only:
        print("[1/2] Running literature search...")
        try:
            run_search(config_path, args)
        except Exception as error:
            print(f"[WARN] Search failed: {error}")
            if args.search_only:
                return 1
            print("[WARN] Continuing to export the latest available results.")

    if not args.search_only:
        print("[2/2] Exporting Word report...")
        try:
            run_export(config_path, args)
        except Exception as error:
            print(f"[ERROR] Export failed: {error}")
            return 1

    print("[OK] Done. Check the reports folder.")
    return 0


def should_pause_on_exit() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    if "--no-pause" in sys.argv or "-h" in sys.argv or "--help" in sys.argv:
        return False
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        if should_pause_on_exit():
            try:
                input("Press Enter to close...")
            except EOFError:
                pass
    raise SystemExit(exit_code)
