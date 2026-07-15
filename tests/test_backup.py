from aicraft import backup, config


def test_run_backup_copia_il_db_e_ritorna_percorso(tmp_path, monkeypatch):
    db_file = tmp_path / "aicraft.db"
    db_file.write_bytes(b"contenuto finto del db")
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_file}")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)

    result = backup.run_backup()

    assert result["ok"]
    backups = list(backup_dir.glob("aicraft_*.db"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"contenuto finto del db"
    assert result["path"] == str(backups[0])


def test_run_backup_senza_db_reale_non_esplode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path / 'non_esiste.db'}")
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")

    result = backup.run_backup()

    assert result["ok"] is False


def test_run_backup_tiene_solo_gli_ultimi_max_backups(tmp_path, monkeypatch):
    db_file = tmp_path / "aicraft.db"
    db_file.write_bytes(b"x")
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_file}")
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(backup, "MAX_BACKUPS", 2)

    for i in range(4):
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / f"aicraft_2026010{i}_000000.db").write_bytes(b"vecchio")

    result = backup.run_backup()

    assert result["ok"]
    remaining = sorted(backup_dir.glob("aicraft_*.db"))
    assert len(remaining) == 2  # i 2 piu' vecchi eliminati, resta il nuovo + il piu' recente tra i vecchi


def test_run_backup_safe_non_solleva_su_errore(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("disco pieno")

    monkeypatch.setattr(backup, "run_backup", boom)

    result = backup.run_backup_safe()

    assert result["ok"] is False
    assert "disco pieno" in result["reason"]
