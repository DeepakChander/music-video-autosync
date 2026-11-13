"""Frozen entry point for autosync-engine.exe (console CLI)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from engine.cli import main
    sys.exit(main())
