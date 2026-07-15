"""Test di download_result: la funzione che scarica in locale un result_url
Higgsfield (gap reale trovato in review 15/07/2026, vedi docs §16 — prima di
questo fix generated_assets teneva solo l'URL remoto, e QA/delivery non
funzionavano mai su un asset vero)."""

import pytest

from aicraft.production import higgsfield_client


def test_download_result_path_locale_non_fa_richieste_di_rete(tmp_path, monkeypatch):
    local_file = tmp_path / "img.png"
    local_file.write_bytes(b"finta")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("non doveva fare richieste di rete per un path locale")

    monkeypatch.setattr(higgsfield_client.requests, "get", fail_if_called)

    result = higgsfield_client.download_result(str(local_file), tmp_path / "dest.png")

    assert result == local_file


def test_download_result_scarica_url_remoto_in_locale(tmp_path, monkeypatch):
    class FakeResponse:
        content = b"contenuto scaricato"

        def raise_for_status(self):
            pass

    seen = {}

    def fake_get(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(higgsfield_client.requests, "get", fake_get)

    dest = tmp_path / "sub" / "video.mp4"
    result = higgsfield_client.download_result("https://cdn.example/video.mp4", dest)

    assert result == dest
    assert dest.read_bytes() == b"contenuto scaricato"
    assert seen["url"] == "https://cdn.example/video.mp4"
    assert seen["timeout"] == 120


def test_download_result_propaga_errore_http(tmp_path, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise higgsfield_client.requests.HTTPError("404")

    monkeypatch.setattr(higgsfield_client.requests, "get", lambda url, timeout=None: FakeResponse())

    with pytest.raises(higgsfield_client.requests.HTTPError):
        higgsfield_client.download_result("https://cdn.example/mancante.mp4", tmp_path / "dest.mp4")


def test_list_recent_jobs_accetta_lista_o_payload_con_jobs(monkeypatch):
    monkeypatch.setattr(
        higgsfield_client,
        "_run_json_raw",
        lambda args: {"jobs": [{"id": "j1", "status": "completed", "result_url": "https://cdn.example/a.mp4", "model": "seedance_2_0"}]},
    )

    jobs = higgsfield_client.list_recent_jobs()

    assert len(jobs) == 1
    assert jobs[0].job_id == "j1"
    assert jobs[0].result_url == "https://cdn.example/a.mp4"


def test_create_json_recupera_job_recente_se_wait_fallisce(monkeypatch):
    def fail(args):
        raise higgsfield_client.HiggsfieldError("wait timeout")

    monkeypatch.setattr(higgsfield_client, "_run_json", fail)
    monkeypatch.setattr(
        higgsfield_client,
        "reconcile_recent_job",
        lambda model: higgsfield_client.GenerationResult(
            job_id="recovered",
            status="completed",
            result_url="https://cdn.example/recovered.mp4",
            cost_credits=None,
            raw={"id": "recovered", "status": "completed", "result_url": "https://cdn.example/recovered.mp4"},
        ),
    )

    data = higgsfield_client._run_create_json_with_reconcile(["generate", "create", "seedance_2_0"], "seedance_2_0")

    assert data["id"] == "recovered"
