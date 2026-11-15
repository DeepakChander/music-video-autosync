"""Ad first cut through the helper (file-name / take-log matching; speech-to-text off so the test
needs no model download)."""

import os
import subprocess
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from helper.app import create_app
from helper.config import TOKEN_HEADER
from tests.conftest import needs_ffmpeg

TOKEN = "a" * 43
SEQ = {"fps_num": 25, "fps_den": 1, "width": 1280, "height": 720}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(TOKEN, str(tmp_path_factory.mktemp("cache")), workers=2)
    with TestClient(app, base_url="http://127.0.0.1:47653", headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


@pytest.fixture(scope="module")
def shoot(tmp_path_factory, ffmpeg_bin):
    d = tmp_path_factory.mktemp("adshoot")
    for name, col in (("1A_T1", "red"), ("1A_T2", "orange"), ("2A_T1", "blue"), ("C0042", "green")):
        subprocess.run([ffmpeg_bin, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={col}:s=320x180:r=25",
                        "-f", "lavfi", "-i", "anoisesrc=a=0.01:r=48000", "-t", "6", "-c:v", "mpeg4", "-c:a", "pcm_s16le",
                        str(d / f"{name}.mov")], check=True)
    (d / "script.csv").write_text("shot,type,description,dialogue,duration\n1A,mos,Wide,,3\n2A,mos,Close,,2\n3A,mos,Hero,,2\n4A,mos,Logo,,2\n",
                                  encoding="utf-8")
    (d / "log.csv").write_text("clip,shot,take,circled\nC0042,3A,4,yes\n", encoding="utf-8")
    return d


def wait(client, url, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(url).json()
        if st.get("state") not in ("running", "queued"):
            return st
        time.sleep(0.2)
    raise AssertionError(f"{url} did not finish")


@needs_ffmpeg
def test_script_check_template_and_speech_status(client, shoot, tmp_path):
    sc = client.post("/ad/script", json={"path": str(shoot / "script.csv")}).json()
    assert [s["shot_id"] for s in sc["shots"]] == ["1A", "2A", "3A", "4A"]
    r = client.post("/ad/script", json={"path": str(tmp_path / "none.csv")})
    assert r.status_code == 404
    t = client.post("/ad/template", json={"path": str(tmp_path)}).json()
    assert os.path.isfile(t["path"]) and "dialogue" in open(t["path"]).read()
    sp = client.get("/ad/speech").json()
    assert set(sp["models"]) == {"tiny.en", "base.en", "small.en"}


@needs_ffmpeg
def test_ad_job_builds_the_cut_in_script_order_and_a_preview(client, shoot):
    clips = [str(shoot / f) for f in sorted(os.listdir(shoot)) if f.endswith(".mov")]
    r = client.post("/ad/jobs", json={"script_path": str(shoot / "script.csv"), "clip_paths": clips, "sequence": SEQ,
                                      "takelog_path": str(shoot / "log.csv"), "target_s": 8, "transcribe": False})
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    st = wait(client, f"/ad/jobs/{jid}")
    assert st["state"] == "done", st
    res = st["result"]
    shots = {s["shot_id"]: s for s in res["shots"]}
    assert shots["1A"]["name"] == "1A_T2.mov" and shots["1A"]["method"] == "file name"
    assert shots["3A"]["name"] == "C0042.mov" and shots["3A"]["method"] == "take log"
    assert shots["4A"]["status"] == "missing" and res["stats"]["missing"] == ["4A"]
    assert res["stats"]["seconds"] == 8.0
    root = ET.parse(res["xml_path"]).getroot()
    names = [m.findtext("name") for m in root.findall("sequence/marker")]
    assert names == ["1A", "2A", "3A", "MISSING 4A"]
    assert len(root.findall("sequence/media/video/track/clipitem")) == 3
    p = client.post(f"/ad/jobs/{jid}/preview", json={"width": 320}).json()
    assert p["state"] == "running"
    pv = wait(client, f"/ad/jobs/{jid}/preview")
    assert pv["state"] == "done", pv
    assert os.path.getsize(pv["path"]) > 1000


def test_ad_job_errors(client, tmp_path):
    r = client.post("/ad/jobs", json={"script_path": str(tmp_path / "x.csv"), "clip_paths": ["a"], "sequence": SEQ})
    assert r.status_code == 404 and r.json()["error"]["code"] == "FILE_NOT_FOUND"
    assert client.get("/ad/jobs/nope").status_code == 404
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    jid = client.post("/ad/jobs", json={"script_path": str(bad), "clip_paths": [str(bad)], "sequence": SEQ,
                                        "transcribe": False}).json()["id"]
    st = wait(client, f"/ad/jobs/{jid}")
    assert st["state"] == "failed" and st["error"]
