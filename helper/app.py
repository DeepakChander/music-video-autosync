"""FastAPI app. Every request needs the X-AutoSync-Token header; Host must be 127.0.0.1/localhost.
Every error is a JSON body {"error": {"code", "message"}} with a non-200 status."""

from __future__ import annotations

import os
import platform
import secrets
import threading
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from engine import __version__ as ENGINE_VERSION
from engine.analyze import MasterError, list_media, load_master, waveform_peaks
from engine.probe import FFmpegNotFound, ProbeError, find_tool, probe
from engine.result import SequenceInfo
from engine.settings import EngineSettings
from export.fcp7_xml import ExportError, XmlOptions, sequence_name, write_xml

from . import __version__
from .config import TOKEN_HEADER, app_data_dir
from .jobs import JobError, JobManager

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MasterReq(_Strict):
    path: str
    peaks: int = Field(600, ge=50, le=4000)


class ProbeReq(_Strict):
    paths: list[str] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)


class SettingsReq(_Strict):
    rates: Optional[list[float]] = None


class JobReq(_Strict):
    master_path: str
    clip_paths: list[str] = Field(min_length=1, max_length=2000)
    sequence: SequenceInfo
    settings: SettingsReq = Field(default_factory=SettingsReq)


class OverrideReq(_Strict):
    song_position_s: Optional[float] = None
    by: str = "editor"
    note: Optional[str] = None


class XmlReq(_Strict):
    sequence_name: Optional[str] = None
    keep_scratch_audio: bool = True
    place_low: bool = False


class SelfTestReq(_Strict):
    count: int = Field(24, ge=4, le=200)
    seed: int = 1
    sequence: SequenceInfo = Field(default_factory=lambda: SequenceInfo(fps_num=24000, fps_den=1001, width=1920, height=1080))


class ReportReq(_Strict):
    name: str = "placement_report"
    report: dict


def create_app(token: str, cache_dir: str, workers: Optional[int] = None) -> FastAPI:
    app = FastAPI(title="Music Video AutoSync helper", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    jobs = JobManager(cache_dir, workers=workers)
    app.state.jobs = jobs
    masters: dict[str, str] = {}
    master_lock = threading.Lock()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return _err(403, "BAD_HOST", "Requests must be addressed to 127.0.0.1")
        given = request.headers.get(TOKEN_HEADER, "")
        if not given or not secrets.compare_digest(given, token):
            return _err(401, "BAD_TOKEN", f"Missing or wrong {TOKEN_HEADER} header")
        return await call_next(request)

    @app.exception_handler(JobError)
    async def _job_error(_req: Request, e: JobError):
        return _err(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", []))
        return _err(422, "VALIDATION_ERROR", f"{where}: {first.get('msg', 'invalid request')}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, e: StarletteHTTPException):
        return _err(e.status_code, "NOT_FOUND" if e.status_code == 404 else "HTTP_ERROR", str(e.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, e: Exception):
        return _err(500, "INTERNAL", f"{type(e).__name__}: {e}")

    @app.get("/health")
    def health():
        tools = {}
        for t in ("ffmpeg", "ffprobe"):
            try:
                tools[t] = find_tool(t)
            except FFmpegNotFound:
                tools[t] = None
        return {"status": "ok", "version": __version__, "engine_version": ENGINE_VERSION,
                "ffmpeg_found": bool(tools["ffmpeg"] and tools["ffprobe"]), "ffmpeg": tools["ffmpeg"],
                "ffprobe": tools["ffprobe"], "cache_dir": cache_dir, "app_data_dir": app_data_dir(),
                "platform": platform.platform(), "pid": os.getpid()}

    @app.get("/settings/defaults")
    def settings_defaults():
        return EngineSettings().public_dict()

    @app.post("/master")
    def master(req: MasterReq):
        if not os.path.isfile(req.path):
            raise JobError("FILE_NOT_FOUND", f"Master not found: {req.path}", 404)
        try:
            with master_lock:
                ctx = load_master(req.path, EngineSettings(), cache_dir)
        except FFmpegNotFound as e:
            raise JobError("FFMPEG_NOT_FOUND", str(e), 500) from e
        except MasterError as e:
            raise JobError("MASTER_ERROR", str(e), 422) from e
        mid = ctx.info.sha256[:16]
        masters[mid] = ctx.info.path
        return {"id": mid, **ctx.info.model_dump(), "peaks": waveform_peaks(ctx.audio, req.peaks)}

    @app.post("/probe")
    def probe_files(req: ProbeReq):
        paths = list(req.paths)
        for folder in req.folders:
            if not os.path.isdir(folder):
                raise JobError("FILE_NOT_FOUND", f"Folder not found: {folder}", 404)
            paths.extend(list_media(folder))
        out = []
        for p in paths:
            try:
                mi = probe(p)
                out.append({"path": os.path.abspath(p), "ok": True, "has_audio": mi.has_audio,
                            "has_video": mi.has_video, "duration_s": round(mi.duration_s, 4), "fps": mi.video_fps})
            except (ProbeError, OSError) as e:
                out.append({"path": os.path.abspath(p), "ok": False, "error": str(e)})
            except FFmpegNotFound as e:
                raise JobError("FFMPEG_NOT_FOUND", str(e), 500) from e
        return {"files": out, "count": len(out), "total_duration_s": round(sum(f.get("duration_s", 0) for f in out), 3)}

    @app.post("/jobs", status_code=201)
    def create_job(req: JobReq):
        if not os.path.isfile(req.master_path):
            raise JobError("FILE_NOT_FOUND", f"Master not found: {req.master_path}", 404)
        missing = [p for p in req.clip_paths if not os.path.exists(p)]
        if missing:
            raise JobError("FILE_NOT_FOUND", f"{len(missing)} clip(s) not found, e.g. {missing[0]}", 404)
        job = jobs.create("analyze", req.master_path, req.clip_paths, req.sequence, req.settings.model_dump())
        return {"job_id": job.id, "state": job.state}

    @app.get("/jobs")
    def list_jobs():
        return {"jobs": [{k: j.data[k] for k in ("id", "kind", "state", "created_at", "finished_at", "progress")}
                         for j in sorted(jobs.jobs.values(), key=lambda j: j.data["created_at"], reverse=True)]}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        j = jobs.get(job_id)
        return {k: j.data[k] for k in ("id", "kind", "state", "created_at", "started_at", "finished_at",
                                       "master_path", "progress", "error", "sequence")}

    @app.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        j = jobs.cancel(job_id)
        return {"job_id": j.id, "state": j.state, "message": "Stopping after the clips in progress; finished results are kept"}

    @app.post("/jobs/{job_id}/resume")
    def resume(job_id: str):
        j = jobs.resume(job_id)
        return {"job_id": j.id, "state": j.state}

    @app.get("/jobs/{job_id}/result")
    def result(job_id: str):
        return jobs.result(job_id).model_dump(mode="json")

    @app.post("/jobs/{job_id}/clips/{clip_id}/override")
    def override(job_id: str, clip_id: str, req: OverrideReq):
        return {"clip": jobs.save_override(job_id, clip_id, req.song_position_s, req.by, req.note)}

    @app.post("/jobs/{job_id}/export/fcp7xml")
    def export_xml(job_id: str, req: XmlReq):
        res = jobs.result(job_id)
        name = req.sequence_name or sequence_name(res.master.path)
        out = os.path.join(jobs.job_dir(job_id), "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name) + ".xml")
        try:
            info = write_xml(res, out, XmlOptions(sequence_name=name, keep_scratch_audio=req.keep_scratch_audio,
                                                   place_low=req.place_low))
        except ExportError as e:
            raise JobError("EXPORT_ERROR", str(e), 422) from e
        return {"sequence_name": name, **info}

    @app.post("/selftest", status_code=201)
    def selftest(req: SelfTestReq):
        job = jobs.create("selftest", None, [], req.sequence, {}, extra={"count": req.count, "seed": req.seed})
        return {"job_id": job.id, "state": job.state}

    @app.get("/jobs/{job_id}/truth")
    def truth(job_id: str):
        j = jobs.get(job_id)
        path = j.data.get("extra", {}).get("truth_csv")
        if j.data["kind"] != "selftest" or not path or not os.path.isfile(path):
            raise JobError("NO_TRUTH", "Only finished self-test jobs have a truth table", 409)
        from helper.selftest import read_truth
        return {"rows": read_truth(path)}

    @app.get("/jobs/{job_id}/accuracy")
    def accuracy(job_id: str):
        """Self-test only: engine positions versus the generated truth (tools/accuracy_report.py)."""
        j = jobs.get(job_id)
        path = j.data.get("extra", {}).get("truth_csv")
        if j.data["kind"] != "selftest" or not path or not os.path.isfile(path):
            raise JobError("NO_TRUTH", "Only finished self-test jobs have a truth table", 409)
        from helper.selftest import read_truth
        from tools.accuracy_report import check_gate, evaluate
        metrics = evaluate(read_truth(path), jobs.result(job_id))["metrics"]
        return {"metrics": metrics, "gate_failures": check_gate(metrics, "synthetic")}

    @app.post("/jobs/{job_id}/reports")
    def save_report(job_id: str, req: ReportReq):
        return {"path": jobs.save_report(job_id, req.name, req.report)}

    return app
