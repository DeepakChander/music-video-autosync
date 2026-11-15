"""Ad first-cut jobs in the helper: background thread per job, status persisted to job.json."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from fractions import Fraction
from typing import Optional

from engine.ad import AdPlan, AdSettings, Clip
from engine.adcut import AdCutError, run_ad_cut
from engine.script import ScriptError


class AdJobError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class AdJobs:
    def __init__(self, cache_dir: str):
        self.root = os.path.join(cache_dir, "ads")
        self.cache_dir = cache_dir
        os.makedirs(self.root, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.previews: dict[str, dict] = {}
        for jid in os.listdir(self.root):  # finished jobs survive a helper restart
            p = os.path.join(self.root, jid, "job.json")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        d = json.load(f)
                    if d.get("state") == "running":
                        d.update(state="interrupted", error="the helper stopped while this job was running")
                    self.jobs[jid] = d
                except Exception:
                    pass

    def folder(self, jid: str) -> str:
        return os.path.join(self.root, jid)

    def _save(self, jid: str) -> None:
        d = self.jobs[jid]
        tmp = os.path.join(self.folder(jid), "job.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, os.path.join(self.folder(jid), "job.json"))

    def get(self, jid: str) -> dict:
        with self.lock:
            d = self.jobs.get(jid)
            if d is None:
                raise AdJobError("AD_JOB_NOT_FOUND", f"No ad job {jid}", 404)
            return json.loads(json.dumps(d))

    def create(self, req: dict) -> dict:
        jid = uuid.uuid4().hex[:12]
        os.makedirs(self.folder(jid), exist_ok=True)
        d = {"id": jid, "state": "running", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": req,
             "progress": {"phase": "starting", "done": 0, "total": 0, "current": ""}, "error": None, "result": None}
        with self.lock:
            self.jobs[jid] = d
            self._save(jid)
        threading.Thread(target=self._run, args=(jid,), daemon=True).start()
        return self.get(jid)

    def _run(self, jid: str) -> None:
        from export.ad_xml import ad_sequence_name, write_ad_xml
        req = self.jobs[jid]["request"]
        seq = req["sequence"]
        fps = Fraction(seq["fps_num"], seq["fps_den"])

        def progress(phase, done, total, current):
            with self.lock:
                self.jobs[jid]["progress"] = {"phase": phase, "done": done, "total": total, "current": current}

        try:
            cut = run_ad_cut(req["script_path"], req["clip_paths"], fps, self.folder(jid),
                             takelog_path=req.get("takelog_path"), music_path=req.get("music_path"),
                             vo_path=req.get("vo_path"), settings=AdSettings(target_s=req.get("target_s")),
                             transcribe=req.get("transcribe", True), model=req.get("model", "base.en"),
                             models_dir=os.path.join(self.cache_dir, "models"), cache_dir=self.cache_dir,
                             analyse_shots=req.get("analyse_shots", False), progress=progress)
            name = req.get("sequence_name") or ad_sequence_name(req["script_path"], req.get("target_s"))
            safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name)
            plan_path = os.path.join(self.folder(jid), safe + ".plan.json")
            with open(plan_path, "w", encoding="utf-8") as f:
                f.write(cut.plan.model_dump_json(indent=2))
            clips_path = os.path.join(self.folder(jid), "clips.json")
            with open(clips_path, "w", encoding="utf-8") as f:
                json.dump([c.__dict__ for c in cut.clips], f)
            info = write_ad_xml(cut.plan, cut.clips, os.path.join(self.folder(jid), safe + ".xml"), name,
                                seq["width"], seq["height"], cut.music_s, cut.vo_s)
            result = {"sequence_name": name, "xml_path": info["path"], "plan_path": plan_path, "clips_path": clips_path,
                      "stats": cut.plan.stats, "warnings": cut.plan.warnings, "transcribed": cut.transcribed,
                      "shots": [{"order": a.order, "shot_id": a.shot_id, "kind": a.kind, "status": a.status, "name": a.name,
                                 "take": a.take, "method": a.method, "confidence": a.confidence, "reason": a.reason,
                                 "seconds": round((a.end - a.start) / float(fps), 3),
                                 "alternates": [x.model_dump() for x in a.alternates]} for a in cut.plan.shots]}
            with self.lock:
                self.jobs[jid].update(state="done", result=result)
                self.jobs[jid]["progress"] = {"phase": "done", "done": 1, "total": 1, "current": ""}
                self._save(jid)
        except (AdCutError, ScriptError, ValueError) as e:
            self._fail(jid, str(e))
        except Exception as e:  # reported to the panel, never swallowed
            self._fail(jid, f"{type(e).__name__}: {e}")

    def _fail(self, jid: str, msg: str) -> None:
        with self.lock:
            self.jobs[jid].update(state="failed", error=msg)
            self._save(jid)

    # ------------------------------------------------------------------ preview
    def start_preview(self, jid: str, width: int) -> dict:
        from export.preview import render_ad_preview
        d = self.get(jid)
        if d["state"] != "done":
            raise AdJobError("AD_NOT_READY", f"The ad job is {d['state']}", 409)
        r = d["result"]
        with open(r["plan_path"], encoding="utf-8") as f:
            plan = AdPlan.model_validate_json(f.read())
        with open(r["clips_path"], encoding="utf-8") as f:
            clips = [Clip(**c) for c in json.load(f)]
        seq = d["request"]["sequence"]
        height = int(round(width * seq["height"] / seq["width"] / 2)) * 2
        mp4 = os.path.splitext(r["xml_path"])[0] + ".preview.mp4"
        with self.lock:
            if self.previews.get(jid, {}).get("state") == "running":
                raise AdJobError("PREVIEW_BUSY", "A preview is already rendering for this job", 409)
            self.previews[jid] = {"state": "running", "done": 0, "total": len(plan.shots), "path": mp4, "error": None}

        def work():
            def prog(done, total):
                with self.lock:
                    self.previews[jid].update(done=done, total=total)
            try:
                render_ad_preview(plan, {c.clip_id: c.path for c in clips}, mp4, width=width, height=height,
                                  workers=max(1, (os.cpu_count() or 2) // 2), progress=prog)
                with self.lock:
                    self.previews[jid]["state"] = "done"
            except Exception as e:  # reported, never swallowed
                with self.lock:
                    self.previews[jid].update(state="failed", error=f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        return self.preview(jid)

    def preview(self, jid: str) -> dict:
        self.get(jid)
        with self.lock:
            st = self.previews.get(jid)
            if st is None:
                raise AdJobError("NO_PREVIEW", "No preview has been started for this ad job", 404)
            return dict(st)
