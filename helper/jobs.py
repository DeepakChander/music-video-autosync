"""Job queue around the engine: progress, cancel, crash-resume, overrides, exports.

State lives in <cache_dir>/jobs/<job_id>/ (job.json + sync_result.json), so a helper that
crashed or was killed can list its jobs again and resume them; clips already analysed come
back from the engine's per-clip cache instantly.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import queue
import threading
import traceback
import uuid
from typing import Any, Optional

from engine.analyze import MasterError, analyze, write_result
from engine.overrides import OverrideError, apply_override
from engine.probe import FFmpegNotFound
from engine.result import SequenceInfo, SyncResult, load_result
from engine.settings import EngineSettings

log = logging.getLogger("autosync.jobs")

TERMINAL = {"done", "cancelled", "failed", "interrupted"}


class JobError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Job:
    def __init__(self, data: dict):
        self.data = data
        self.cancel = threading.Event()
        self.lock = threading.RLock()

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def state(self) -> str:
        return self.data["state"]


class JobManager:
    def __init__(self, cache_dir: str, workers: Optional[int] = None):
        self.cache_dir = cache_dir
        self.jobs_dir = os.path.join(cache_dir, "jobs")
        self.workers = workers
        os.makedirs(self.jobs_dir, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[str]" = queue.Queue()
        self._load_existing()
        self._thread = threading.Thread(target=self._run_loop, name="autosync-jobs", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- persistence
    def job_dir(self, job_id: str) -> str:
        return os.path.join(self.jobs_dir, job_id)

    def _save(self, job: Job) -> None:
        d = self.job_dir(job.id)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, "job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job.data, f, indent=2)
        os.replace(tmp, os.path.join(d, "job.json"))

    def _load_existing(self) -> None:
        for name in sorted(os.listdir(self.jobs_dir)):
            p = os.path.join(self.jobs_dir, name, "job.json")
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                log.warning("unreadable job file %s", p)
                continue
            job = Job(data)
            if job.state in ("queued", "running"):
                # The previous helper process died with this job unfinished.
                job.data["state"] = "interrupted"
                job.data["error"] = {"code": "INTERRUPTED",
                                     "message": "The helper stopped before this job finished. Resume it to continue from cache."}
                self._save(job)
            self.jobs[job.id] = job

    # ---------------------------------------------------------------- API
    def create(self, kind: str, master_path: Optional[str], clip_paths: list[str], sequence: SequenceInfo,
               settings: dict, extra: Optional[dict] = None) -> Job:
        job_id = uuid.uuid4().hex[:12]
        data = {
            "id": job_id, "kind": kind, "state": "queued", "created_at": _now(), "started_at": None,
            "finished_at": None, "master_path": master_path, "clip_paths": clip_paths,
            "sequence": sequence.model_dump(), "settings": settings,
            "progress": {"phase": "queued", "total": len(clip_paths), "done": 0, "HIGH": 0, "MEDIUM": 0,
                         "LOW": 0, "UNSYNCED": 0, "current": None, "cache_hits": 0},
            "error": None, "result_path": None, "extra": extra or {},
        }
        job = Job(data)
        self.jobs[job_id] = job
        self._save(job)
        self.q.put(job_id)
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise JobError("JOB_NOT_FOUND", f"No job {job_id}", 404)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state in TERMINAL:
            raise JobError("JOB_NOT_RUNNING", f"Job is {job.state}; nothing to cancel", 409)
        job.cancel.set()
        with job.lock:
            job.data["progress"]["phase"] = "cancelling"
            self._save(job)
        return job

    def resume(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state not in ("interrupted", "cancelled", "failed"):
            raise JobError("JOB_NOT_RESUMABLE", f"Job is {job.state}", 409)
        job.cancel = threading.Event()
        with job.lock:
            job.data.update({"state": "queued", "error": None, "finished_at": None})
            job.data["progress"]["phase"] = "queued"
            self._save(job)
        self.q.put(job.id)
        return job

    def result(self, job_id: str) -> SyncResult:
        job = self.get(job_id)
        path = job.data.get("result_path")
        if not path or not os.path.isfile(path):
            raise JobError("RESULT_NOT_READY", f"Job is {job.state}; no result yet", 409)
        return load_result(path)

    def save_override(self, job_id: str, clip_id: str, song_position_s: Optional[float], by: str,
                      note: Optional[str]) -> dict:
        job = self.get(job_id)
        with job.lock:
            res = self.result(job_id)
            before = {c.clip_id: c.model_dump(exclude={"override"}) for c in res.clips}
            try:
                clip = apply_override(res, clip_id, song_position_s, by, note)
            except OverrideError as e:
                raise JobError("BAD_OVERRIDE", str(e), 404 if "unknown clip" in str(e) else 400) from e
            after = {c.clip_id: c.model_dump(exclude={"override"}) for c in res.clips}
            if before != after:  # rule 6: overrides never overwrite engine fields
                raise JobError("INTERNAL", "override changed engine fields; refused", 500)
            write_result(res, job.data["result_path"])
            return clip.model_dump(mode="json")

    def save_report(self, job_id: str, name: str, report: dict) -> str:
        job = self.get(job_id)
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "report"
        path = os.path.join(self.job_dir(job.id), f"{safe}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return path

    # ---------------------------------------------------------------- worker
    def _run_loop(self) -> None:
        while True:
            job_id = self.q.get()
            job = self.jobs.get(job_id)
            if job is None or job.state != "queued":
                continue
            try:
                self._run(job)
            except Exception:  # never let the worker thread die
                log.exception("job %s crashed", job_id)
                with job.lock:
                    job.data["state"] = "failed"
                    job.data["error"] = {"code": "INTERNAL", "message": traceback.format_exc(limit=3)}
                    job.data["finished_at"] = _now()
                    self._save(job)

    def _progress(self, job: Job, ev: dict[str, Any]) -> None:
        with job.lock:
            p = job.data["progress"]
            for k in ("total", "done", "HIGH", "MEDIUM", "LOW", "UNSYNCED", "cache_hits"):
                if k in ev:
                    p[k] = ev[k]
            if ev.get("current"):
                p["current"] = ev["current"]
            self._save(job)

    def _run(self, job: Job) -> None:
        with job.lock:
            job.data["state"] = "running"
            job.data["started_at"] = job.data["started_at"] or _now()
            self._save(job)
        if job.data["kind"] == "selftest" and not job.data["extra"].get("generated"):
            from helper.selftest import generate  # heavy import only when needed
            with job.lock:
                job.data["progress"]["phase"] = "generating test media"
                self._save(job)
            gen = generate(self.job_dir(job.id), job.data["extra"]["count"], job.data["extra"]["seed"],
                           cancel=job.cancel, progress=lambda done, total: self._progress(job, {"done": done, "total": total}))
            if job.cancel.is_set():
                self._finish(job, "cancelled")
                return
            with job.lock:
                job.data["master_path"] = gen["master"]
                job.data["clip_paths"] = gen["clips"]
                job.data["extra"].update({"generated": True, "truth_csv": gen["truth_csv"]})
                job.data["progress"].update({"total": len(gen["clips"]), "done": 0})
                self._save(job)

        with job.lock:
            job.data["progress"]["phase"] = "analysing"
            self._save(job)
        overrides = {k: v for k, v in (job.data.get("settings") or {}).items() if v is not None}
        if "rates" in overrides:
            overrides["rates"] = tuple(float(r) for r in overrides["rates"])
        try:
            s = EngineSettings(**overrides)
            seq = SequenceInfo(**job.data["sequence"])
            res = analyze(job.data["master_path"], job.data["clip_paths"], seq, s, cache_dir=self.cache_dir,
                          workers=self.workers, progress=lambda ev: self._progress(job, ev), cancel=job.cancel)
        except (MasterError, FFmpegNotFound, ValueError) as e:
            with job.lock:
                job.data["error"] = {"code": "MASTER_ERROR" if isinstance(e, MasterError) else
                                     "FFMPEG_NOT_FOUND" if isinstance(e, FFmpegNotFound) else "BAD_SETTINGS",
                                     "message": str(e)}
            self._finish(job, "failed")
            return
        path = os.path.join(self.job_dir(job.id), "sync_result.json")
        old = job.data.get("result_path")
        if old and os.path.isfile(old):
            # Keep manual overrides across a resume: they are not engine fields.
            try:
                prev = load_result(old)
                keep = {c.clip_id: c.override for c in prev.clips if c.override is not None}
                for c in res.clips:
                    if c.clip_id in keep:
                        c.override = keep[c.clip_id]
            except Exception:
                log.warning("could not carry overrides from %s", old)
        write_result(res, path)
        with job.lock:
            job.data["result_path"] = path
            sm = res.summary
            job.data["progress"].update({"done": sm.total - sum(1 for c in res.clips if c.reason and c.reason.value == "NOT_ANALYZED"),
                                         "HIGH": sm.HIGH, "MEDIUM": sm.MEDIUM, "LOW": sm.LOW, "UNSYNCED": sm.UNSYNCED,
                                         "current": None})
        self._finish(job, "cancelled" if job.cancel.is_set() else "done")

    def _finish(self, job: Job, state: str) -> None:
        with job.lock:
            job.data["state"] = state
            job.data["progress"]["phase"] = state
            job.data["finished_at"] = _now()
            self._save(job)
