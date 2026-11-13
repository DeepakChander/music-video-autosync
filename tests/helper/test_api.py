import os
import time

import pytest
from fastapi.testclient import TestClient

from engine.analyze import list_media
from engine.result import SyncResult
from helper.app import create_app
from helper.config import TOKEN_HEADER

TOKEN = "t" * 43
SEQ = {"fps_num": 24000, "fps_den": 1001, "width": 1920, "height": 1080}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(TOKEN, str(tmp_path_factory.mktemp("cache")), workers=2)
    with TestClient(app, base_url="http://127.0.0.1:47653", headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


def wait(client, job_id, states=("done", "cancelled", "failed"), timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/jobs/{job_id}").json()
        if st["state"] in states:
            return st
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish: {st}")


def test_token_required(client):
    r = client.get("/health", headers={TOKEN_HEADER: ""})
    assert r.status_code == 401 and r.json()["error"]["code"] == "BAD_TOKEN"
    r = client.get("/health", headers={TOKEN_HEADER: "wrong"})
    assert r.status_code == 401


def test_host_must_be_loopback(client):
    r = client.get("/health", headers={"host": "evil.example:47653"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "BAD_HOST"


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["ffmpeg_found"] is True and h["engine_version"]


def test_errors_are_json_and_never_200(client):
    r = client.post("/master", json={"path": "/no/such/file.wav"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "FILE_NOT_FOUND"
    r = client.post("/jobs", json={"master_path": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.get("/jobs/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_master_and_probe(client, synth_set):
    m = client.post("/master", json={"path": synth_set["master"], "peaks": 200}).json()
    assert len(m["peaks"]) == 200 and m["duration_s"] > 200 and len(m["sha256"]) == 64
    p = client.post("/probe", json={"folders": [os.path.join(synth_set["dir"], "takes")]}).json()
    assert p["count"] == len(synth_set["truth"])
    assert any(not f["ok"] for f in p["files"])  # the corrupt take is reported, not dropped


def test_job_override_and_export(client, synth_set, tmp_path):
    clips = list_media(os.path.join(synth_set["dir"], "takes"))
    r = client.post("/jobs", json={"master_path": synth_set["master"], "clip_paths": clips, "sequence": SEQ})
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    st = wait(client, job_id)
    assert st["state"] == "done" and st["progress"]["done"] == len(clips)
    res = SyncResult.model_validate(client.get(f"/jobs/{job_id}/result").json())
    assert len(res.clips) == len(clips)

    # override an UNSYNCED clip: engine fields stay, override carries the position + track
    target = next(c for c in res.clips if c.status.value == "UNSYNCED")
    before = target.model_dump(exclude={"override"})
    r = client.post(f"/jobs/{job_id}/clips/{target.clip_id}/override", json={"song_position_s": 12.3, "by": "tester"})
    assert r.status_code == 200, r.text
    res2 = SyncResult.model_validate(client.get(f"/jobs/{job_id}/result").json())
    c2 = res2.find(target.clip_id)
    assert c2.model_dump(exclude={"override"}) == before
    assert c2.override.by == "tester" and c2.override.video_track >= 1
    assert client.post(f"/jobs/{job_id}/clips/{target.clip_id}/override", json={"song_position_s": -1}).status_code == 400
    assert client.post(f"/jobs/{job_id}/clips/nope/override", json={"song_position_s": 1}).status_code == 404

    x = client.post(f"/jobs/{job_id}/export/fcp7xml", json={"sequence_name": "AutoSync - test"}).json()
    assert os.path.isfile(x["path"]) and x["clips_placed"] >= 1

    # clear the override
    client.post(f"/jobs/{job_id}/clips/{target.clip_id}/override", json={"song_position_s": None})
    assert SyncResult.model_validate(client.get(f"/jobs/{job_id}/result").json()).find(target.clip_id).override is None


def test_cancel_keeps_every_clip(client, synth_set):
    clips = list_media(os.path.join(synth_set["dir"], "takes"))
    job_id = client.post("/jobs", json={"master_path": synth_set["master"], "clip_paths": clips,
                                        "sequence": {**SEQ, "fps_num": 25, "fps_den": 1}}).json()["job_id"]
    client.post(f"/jobs/{job_id}/cancel")
    st = wait(client, job_id)
    assert st["state"] in ("cancelled", "done")
    res = client.get(f"/jobs/{job_id}/result").json()
    assert len(res["clips"]) == len(clips)


def test_selftest_job_and_accuracy(client):
    job_id = client.post("/selftest", json={"count": 14, "seed": 3}).json()["job_id"]
    st = wait(client, job_id, timeout=600)
    assert st["state"] == "done", st
    acc = client.get(f"/jobs/{job_id}/accuracy").json()
    assert acc["gate_failures"] == [], acc
    assert len(client.get(f"/jobs/{job_id}/truth").json()["rows"]) == 14


def test_remaining_endpoints(client, synth_set):
    d = client.get("/settings/defaults").json()
    assert d["noise_floor"] > 0 and d["rates"] == [1.0, 2.0]
    jobs = client.get("/jobs").json()["jobs"]
    assert jobs and {"id", "state", "progress"} <= set(jobs[0])
    job_id = jobs[0]["id"]
    r = client.post(f"/jobs/{job_id}/reports", json={"name": "placement_report", "report": {"mismatches": 0}})
    assert r.status_code == 200 and os.path.isfile(r.json()["path"])
    assert client.post(f"/jobs/{job_id}/resume").status_code == 409   # done jobs are not resumable
    assert client.get(f"/jobs/{job_id}/truth").status_code in (200, 409)
