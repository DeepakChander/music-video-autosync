"""Self-test media: a synthetic song plus takes with known truth, generated on the client's
machine with the bundled FFmpeg. Used by the panel's placement self-test (Phase 0 spikes b/c on
the client's own Premiere) and as an end-to-end smoke test of the whole pipeline."""

from __future__ import annotations

import csv
import os
import threading
from typing import Callable, Optional

from tools.make_synthetic_takes import build


def generate(job_dir: str, count: int, seed: int, cancel: Optional[threading.Event] = None,
             progress: Optional[Callable[[int, int], None]] = None) -> dict:
    out = os.path.join(job_dir, "selftest_media")
    takes = build(out, count=count, seed=seed, master_path=None, video_codec="prores", container="mov",
                  size="320x180", progress=progress, cancel=cancel)
    with open(os.path.join(out, "master_path.txt"), "r", encoding="utf-8") as f:
        master = f.read().strip()
    clips = [os.path.abspath(os.path.join(out, t.file)) for t in takes]
    return {"master": master, "clips": clips, "truth_csv": os.path.join(out, "truth.csv")}


def read_truth(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
