import plistlib

from aicraft import scheduler


def test_launchd_plist_contiene_sync_policy(tmp_path):
    plist = scheduler.launchd_plist(project_dir=tmp_path, python_bin="/venv/bin/python", weekday=2, hour=9, minute=30)

    assert plist["Label"] == scheduler.DEFAULT_LABEL
    assert plist["ProgramArguments"] == ["/venv/bin/python", "-m", "aicraft.cli", "references", "sync-policy"]
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert plist["StartCalendarInterval"] == {"Weekday": 2, "Hour": 9, "Minute": 30}


def test_install_weekly_sync_scrive_plist(tmp_path):
    target_dir = tmp_path / "LaunchAgents"

    path = scheduler.install_weekly_sync(
        launch_agents_dir=target_dir,
        project_dir=tmp_path,
        python_bin="/venv/bin/python",
    )

    data = plistlib.loads(path.read_bytes())
    assert path == target_dir / f"{scheduler.DEFAULT_LABEL}.plist"
    assert data["ProgramArguments"][-2:] == ["references", "sync-policy"]
