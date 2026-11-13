"""Frozen entry point for AutoSyncHelper.exe (windowed: tray app + service)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()  # must run first: the engine's process pool re-launches this exe
    from helper.__main__ import main
    sys.exit(main())
