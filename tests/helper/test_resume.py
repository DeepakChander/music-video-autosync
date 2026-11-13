"""A killed helper resumes a job from cache (Phase 2 gate)."""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from engine.analyze import list_media
from tests.conftest import ROOT, needs_ffmpeg


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(env):
    return subprocess.Popen([sys.executable, "-m", "helper", "serve"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_up(url, headers, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(url + "/health", headers=headers, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("helper did not start")


@needs_ffmpeg
@pytest.mark.skipif(os.name == "nt", reason="uses SIGKILL")
def test_killed_job_resumes_from_cache(synth_set, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"workers": 1}))
    port = _free_port()
    env = {**os.environ, "AUTOSYNC_HOME": str(home), "AUTOSYNC_CACHE": str(tmp_path / "cache"), "AUTOSYNC_PORT": str(port)}
    url = f"http://127.0.0.1:{port}"
    subprocess.run([sys.executable, "-m", "helper", "init-token"], cwd=ROOT, env=env, check=True, capture_output=True)
    headers = {"X-AutoSync-Token": (home / "token").read_text().strip()}
    clips = list_media(os.path.join(synth_set["dir"], "takes"))

    p1 = _start(env)
    try:
        _wait_up(url, headers)
        job_id = httpx.post(url + "/jobs", headers=headers, timeout=10, json={
            "master_path": synth_set["master"], "clip_paths": clips,
            "sequence": {"fps_num": 30000, "fps_den": 1001}}).json()["job_id"]
        t0 = time.time()
        while True:
            st = httpx.get(f"{url}/jobs/{job_id}", headers=headers, timeout=5).json()
            if st["progress"]["done"] >= 6:
                break
            assert time.time() - t0 < 300 and st["state"] == "running", st
            time.sleep(0.1)
        done_before_kill = st["progress"]["done"]
    finally:
        p1.send_signal(signal.SIGKILL)
        p1.wait()

    p2 = _start(env)
    try:
        _wait_up(url, headers)
        st = httpx.get(f"{url}/jobs/{job_id}", headers=headers, timeout=5).json()
        assert st["state"] == "interrupted" and st["error"]["code"] == "INTERRUPTED"
        assert httpx.post(f"{url}/jobs/{job_id}/resume", headers=headers, timeout=5).status_code == 200
        t0 = time.time()
        while st["state"] not in ("done", "failed"):
            assert time.time() - t0 < 300
            time.sleep(0.2)
            st = httpx.get(f"{url}/jobs/{job_id}", headers=headers, timeout=5).json()
        assert st["state"] == "done", st
        assert st["progress"]["cache_hits"] >= done_before_kill
        res = httpx.get(f"{url}/jobs/{job_id}/result", headers=headers, timeout=10).json()
        assert len(res["clips"]) == len(clips)
        assert not any(c["reason"] == "NOT_ANALYZED" for c in res["clips"])
    finally:
        p2.terminate()
        p2.wait(timeout=10)
