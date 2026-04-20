"""Tests for the async job API and persistence layer (offline, stub LLM)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import jobs as jobs_mod
from app.llm.client import set_stub_translator
from tests.make_fixtures import make_all
from tests.stub_llm import stub_translate


@pytest.fixture(scope="session", autouse=True)
def _fixtures():
    make_all()


@pytest.fixture(autouse=True)
def _stub():
    set_stub_translator(stub_translate)
    yield
    set_stub_translator(None)


def test_job_lifecycle(tmp_path, monkeypatch):
    # Redirect jobs root to a temp dir so test doesn't touch user state.
    monkeypatch.setattr(jobs_mod.settings, "temp_dir", tmp_path)
    jid = jobs_mod.new_job_id()
    assert len(jid) == 16
    meta = jobs_mod.JobMeta(
        id=jid, status="queued", target_lang="ar",
        provider="stub", model="stub", output_mode="translated",
        input_name="sample.txt",
    )
    jobs_mod.save_meta(meta)
    loaded = jobs_mod.load_meta(jid)
    assert loaded is not None
    assert loaded.id == jid
    jobs_mod.update_status(jid, status="done", progress=1.0, output_name="out.txt")
    updated = jobs_mod.load_meta(jid)
    assert updated.status == "done"
    assert updated.progress == 1.0
    assert updated.output_name == "out.txt"
    # list
    all_jobs = jobs_mod.list_jobs()
    assert any(j.id == jid for j in all_jobs)
    # delete
    assert jobs_mod.delete_job(jid)
    assert jobs_mod.load_meta(jid) is None


def test_create_job_via_httpapi(tmp_path, monkeypatch):
    """End-to-end HTTP: POST /api/jobs -> poll -> download."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(jobs_mod.settings, "temp_dir", tmp_path)
    from app.main import create_app

    app = create_app()
    client = TestClient(app)

    fpath = Path(__file__).parent / "fixtures" / "sample.txt"
    with fpath.open("rb") as f:
        resp = client.post(
            "/api/jobs",
            files={"file": ("sample.txt", f, "text/plain")},
            data={
                "target_lang": "es",
                "provider": "stub",
                "model": "stub",
                "api_key": "stub",
                "output_mode": "translated",
            },
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    jid = body["id"]
    assert body["status_url"] == f"/api/jobs/{jid}"
    assert body["download_url"] == f"/api/jobs/{jid}/download"

    # Poll until done (async task runs in the same event loop).
    import time as _time

    for _ in range(60):
        r = client.get(f"/api/jobs/{jid}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] == "done":
            break
        if data["status"] == "failed":
            pytest.fail(f"job failed: {data.get('error')}")
        _time.sleep(0.1)
    else:
        pytest.fail("job did not complete in time")

    dl = client.get(f"/api/jobs/{jid}/download")
    assert dl.status_code == 200
    assert b"[es]" in dl.content
