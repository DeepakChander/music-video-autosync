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


def _wait_state(client, url, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(url).json()
        if st.get("state") != "running":
            return st
        time.sleep(0.3)
    raise AssertionError(f"{url} still running")


def test_colour_match_needs_the_shot_analysis_first(client, tmp_path_factory, synth_set):
    clips = [c for c in list_media(synth_set["dir"]) if os.path.abspath(c) != os.path.abspath(synth_set["master"])][:4]
    jid = client.post("/jobs", json={"master_path": synth_set["master"], "clip_paths": clips, "sequence": SEQ}).json()["job_id"]
    t0 = time.time()
    while client.get(f"/jobs/{jid}").json()["state"] not in ("done", "failed") and time.time() - t0 < 300:
        time.sleep(0.2)
    r = client.post(f"/jobs/{jid}/cut", json={"colour": True})
    assert r.status_code == 409 and "shot analysis" in r.json()["error"]["message"]
    assert client.get(f"/jobs/{jid}/shots").status_code == 404


def test_shot_analysis_then_cut_with_colour_and_look(client, job):
    r = client.post(f"/jobs/{job}/shots", json={"faces": True})
    assert r.status_code == 200 and r.json()["state"] == "running"
    st = _wait_state(client, f"/jobs/{job}/shots")
    assert st["state"] == "done", st
    assert st["takes"] and all("size" in t and "setup" in t for t in st["takes"])
    out = client.post(f"/jobs/{job}/cut", json={"style": "performance", "colour": True, "look": "warm",
                                               "preview": True, "preview_width": 320}).json()
    assert out["used_shot_analysis"] is True
    col = out["colour"]
    assert col["hero"] and col["look"] == "warm" and os.path.isfile(col["look_lut"])
    for t in col["takes"].values():
        assert os.path.isfile(t["match_lut"]) and os.path.isfile(t["final_lut"])
        assert t["delta_e_after"] <= t["delta_e_before"] + 1e-6
    xml = open(out["xml_path"], encoding="utf-8").read()
    assert "Input LUT:" in xml
    pv = _wait_state(client, f"/jobs/{job}/cut/preview")
    assert pv["state"] == "done", pv
    r = client.post(f"/jobs/{job}/cut", json={"look": "sepia"})
    assert r.status_code == 422


def test_social_versions_and_lut_install(client, job, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYNC_LUT_DIR", str(tmp_path / "LUTs"))
    if client.get(f"/jobs/{job}/shots").status_code == 404:
        client.post(f"/jobs/{job}/shots", json={"faces": True})
        _wait_state(client, f"/jobs/{job}/shots")
    out = client.post(f"/jobs/{job}/cut", json={"colour": True, "look": "cool", "install_luts": True,
                                               "aspects": ["9:16", "1:1"]}).json()
    assert [v["aspect"] for v in out["versions"]] == ["9:16", "1:1"]
    for v in out["versions"]:
        root = ET.parse(v["xml_path"]).getroot()
        fmt = root.find("sequence/media/video/format/samplecharacteristics")
        assert (int(fmt.findtext("width")), int(fmt.findtext("height"))) == (v["width"], v["height"])
        assert root.find(".//clipitem/filter/effect[effectid='basic']") is not None
    li = out["lut_install"]
    assert li and not li.get("error") and li["files"] and all(os.path.isfile(f) for f in li["files"])
    assert os.path.isfile(li["look"])
    r = client.post(f"/jobs/{job}/cut", json={"aspects": ["21:9"]})
    assert r.status_code == 422
