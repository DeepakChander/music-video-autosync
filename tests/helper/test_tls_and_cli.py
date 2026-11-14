"""HTTPS helper (macOS route) and the command-line route (analyse -> report -> XML)."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest

from tests.conftest import ROOT, needs_ffmpeg


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_helper_serves_https_with_configured_certificate(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"tls_cert": str(cert), "tls_key": str(key)}))
    port = _free_port()
    env = {**os.environ, "AUTOSYNC_HOME": str(home), "AUTOSYNC_CACHE": str(tmp_path / "cache"), "AUTOSYNC_PORT": str(port)}
    subprocess.run([sys.executable, "-m", "helper", "init-token"], cwd=ROOT, env=env, check=True, capture_output=True)
    token = (home / "token").read_text().strip()
    proc = subprocess.Popen([sys.executable, "-m", "helper", "serve"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        t0 = time.time()
        while True:
            try:
                r = httpx.get(f"https://127.0.0.1:{port}/health", headers={"X-AutoSync-Token": token}, verify=str(cert), timeout=2)
                break
            except httpx.HTTPError:
                assert time.time() - t0 < 30, "helper did not come up over https"
                time.sleep(0.3)
        assert r.status_code == 200 and r.json()["status"] == "ok"
        with pytest.raises(httpx.HTTPError):  # plain http is not served on that port
            httpx.get(f"http://127.0.0.1:{port}/health", headers={"X-AutoSync-Token": token}, timeout=2)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@needs_ffmpeg
def test_command_line_route_report_and_xml(synth_set, tmp_path):
    out = tmp_path / "sync_result.json"
    subprocess.run([sys.executable, "-m", "engine.cli", "analyze", "--master", synth_set["master"], "--clips",
                    os.path.join(synth_set["dir"], "takes"), "--fps", "25", "--out", str(out), "--no-cache", "--quiet"],
                   cwd=ROOT, check=True, capture_output=True)
    rep = subprocess.run([sys.executable, "-m", "engine.cli", "report", "--result", str(out)], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    assert "UNSYNCED" in rep and "NO_AUDIO" in rep and "clips:" in rep
    xml = tmp_path / "draft.xml"
    msg = subprocess.run([sys.executable, "-m", "export.fcp7_xml", "--result", str(out), "--out", str(xml)], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    assert xml.is_file() and xml.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>') and "Wrote" in msg
