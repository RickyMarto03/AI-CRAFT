"""Smoke test della CLI end-to-end su un DB temporaneo reale (nessuna rete:
si usano solo comandi che non chiamano Higgsfield/Claude — approve/produce
hitterebbero i servizi esterni e sono coperti dai test dei moduli)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aicraft import cli
from aicraft.db.base import Base


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cli.db'}")
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(cli, "SessionLocal", TestSession)
    monkeypatch.setattr(cli, "init_db", lambda: Base.metadata.create_all(engine))
    return engine


def test_cli_flusso_base(cli_env, capsys):
    cli.main(["profiles", "add-creator", "Trinity"])
    cli.main(["profiles", "add", "1", "Ruby Wilde", "misto"])
    cli.main(["profiles", "use", "1"])
    cli.main(["budget", "topup", "100", "--motivo", "test"])
    cli.main(["plan", "create", "1", "2026-07-20", "2026-07-26"])
    cli.main(["plan", "add", "1", "carosello", "--giorno", "lun"])
    capsys.readouterr()  # svuota

    cli.main(["plan", "show", "1"])
    show_out = capsys.readouterr().out
    assert "carosello" in show_out
    assert "bozza" in show_out

    cli.main(["status"])
    status_out = capsys.readouterr().out
    assert "Ruby Wilde" in status_out
    assert "100.00" in status_out
    assert "Command Center" in status_out


def test_cli_profilo_attivo_marcato(cli_env, capsys):
    cli.main(["profiles", "add-creator", "Trinity"])
    cli.main(["profiles", "add", "1", "Ruby", "misto"])
    cli.main(["profiles", "add", "1", "Nova", "solo_talking"])
    cli.main(["profiles", "use", "2"])
    capsys.readouterr()

    cli.main(["profiles", "list"])
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if "Nova" in l]
    assert lines and lines[0].startswith("*")  # profilo attivo marcato con *


def test_cli_scheduler_plist(cli_env, capsys):
    cli.main(["scheduler", "plist", "--weekday", "2", "--hour", "9", "--minute", "15"])

    out = capsys.readouterr().out
    assert "com.aicraft.weekly-reference-sync" in out
    assert "sync-policy" in out
