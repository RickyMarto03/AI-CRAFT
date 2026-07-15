from pathlib import Path

from aicraft import config


def test_augment_path_aggiunge_npm_global_bin_se_presente(monkeypatch, tmp_path):
    fake_home = tmp_path
    npm_global_bin = fake_home / ".npm-global" / "bin"
    npm_global_bin.mkdir(parents=True)

    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(config.os, "environ", {"PATH": "/usr/bin:/bin"})

    config._augment_path_for_gui_apps()

    parts = config.os.environ["PATH"].split(config.os.pathsep)
    assert str(npm_global_bin) in parts
    assert "/usr/bin" in parts  # il PATH originale non viene perso


def test_augment_path_non_duplica_una_cartella_gia_presente(monkeypatch, tmp_path):
    fake_home = tmp_path
    npm_global_bin = fake_home / ".npm-global" / "bin"
    npm_global_bin.mkdir(parents=True)

    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(config.os, "environ", {"PATH": f"/usr/bin:{npm_global_bin}"})

    config._augment_path_for_gui_apps()

    parts = config.os.environ["PATH"].split(config.os.pathsep)
    assert parts.count(str(npm_global_bin)) == 1


def test_augment_path_ignora_cartelle_home_inesistenti(monkeypatch, tmp_path):
    # nessuna sottocartella creata sotto fake_home: i due candidati legati
    # alla home (.npm-global/bin, .local/bin) non esistono, quindi non
    # vengono aggiunti — non asseriamo sul PATH finale perche' i due
    # candidati assoluti (/opt/homebrew/bin, /usr/local/bin) dipendono
    # dalla macchina che esegue il test, non dalla home finta.
    fake_home = tmp_path
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(config.os, "environ", {"PATH": "/usr/bin"})

    config._augment_path_for_gui_apps()

    parts = config.os.environ["PATH"].split(config.os.pathsep)
    assert str(fake_home / ".npm-global" / "bin") not in parts
    assert str(fake_home / ".local" / "bin") not in parts
