"""First cut through the helper: music map, cut (plan + XML), background preview."""

import os
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from engine.analyze import list_media
from helper.app import create_app
from helper.config import TOKEN_HEADER

TOKEN = "c" * 43
SEQ = {"fps_num": 24000, "fps_den": 1001, "width": 1920, "height": 1080}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(TOKEN, str(tmp_path_factory.mktemp("cache")), workers=2)
    with TestClient(app, base_url="http://127.0.0.1:47653", headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


@pytest.fixture(scope="module")
def job(client, synth_set):
    clips = list_media(synth_set["dir"])
    clips = [c for c in clips if os.path.abspath(c) != os.path.abspath(synth_set["master"])][:16]
    r = client.post("/jobs", json={"master_path": synth_set["master"], "clip_paths": clips, "sequence": SEQ})
    assert r.status_code == 201
    jid = r.json()["job_id"]
    t0 = time.time()
    while client.get(f"/jobs/{jid}").json()["state"] not in ("done", "failed") and time.time() - t0 < 300:
        time.sleep(0.2)
    assert client.get(f"/jobs/{jid}").json()["state"] == "done"
    return jid


def test_music_map(client, job):
    mm = client.get(f"/jobs/{job}/music").json()
    assert mm["tempo_bpm"] > 0 and mm["beats"] and mm["sections"]


def test_cut_writes_plan_and_xml_and_renders_a_preview(client, job):
    r = client.post(f"/jobs/{job}/cut", json={"style": "hype", "preview": True, "preview_width": 320})
    assert r.status_code == 200, r.text
    out = r.json()
    assert os.path.isfile(out["xml_path"]) and os.path.isfile(out["plan_path"])
    root = ET.parse(out["xml_path"]).getroot()
    assert root.find("sequence/name").text == out["sequence_name"]
    assert out["stats"]["shots"] > 0 and out["markers"]
    t0 = time.time()
    while time.time() - t0 < 300:
        st = client.get(f"/jobs/{job}/cut/preview").json()
        if st["state"] != "running":
            break
        time.sleep(0.3)
    assert st["state"] == "done", st
    assert os.path.getsize(st["path"]) > 1000


def test_cut_rejects_bad_settings_and_unknown_jobs(client, job):
    r = client.post(f"/jobs/{job}/cut", json={"style": "dubstep"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = client.post("/jobs/nope/cut", json={})
    assert r.status_code == 404
    r = client.get("/jobs/nope/cut/preview")
    assert r.status_code == 404
