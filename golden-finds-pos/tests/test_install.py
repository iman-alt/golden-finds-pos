"""
Pieces the Windows installer relies on.
"""

import os
from pathlib import Path

from app.config import load_local_env

WINDOWS = Path(__file__).resolve().parent.parent / "windows"


def test_local_settings_are_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("GF_TEST_BACKUP_DIR", raising=False)
    settings = tmp_path / "local.env"
    settings.write_text(
        "# a comment\n\nGF_TEST_BACKUP_DIR = C:\\Users\\Mum\\OneDrive\\Golden Finds backups\n",
        encoding="utf-8",
    )
    load_local_env(settings)
    assert os.environ["GF_TEST_BACKUP_DIR"] == r"C:\Users\Mum\OneDrive\Golden Finds backups"


def test_a_real_environment_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GF_TEST_BACKUP_DIR", "from-environment")
    settings = tmp_path / "local.env"
    settings.write_text("GF_TEST_BACKUP_DIR=from-file\n", encoding="utf-8")
    load_local_env(settings)
    assert os.environ["GF_TEST_BACKUP_DIR"] == "from-environment"


def test_a_missing_settings_file_is_fine(tmp_path):
    load_local_env(tmp_path / "nope.env")


def test_the_icon_can_be_drawn(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_icon", WINDOWS / "make_icon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "icon.ico"
    module.draw().save(target, sizes=[(32, 32), (256, 256)])
    assert target.stat().st_size > 0


def test_windows_scripts_use_crlf_and_plain_ascii():
    """
    .bat files misbehave with Unix line endings, and Windows PowerShell 5.1
    misreads UTF-8 without a BOM - so these must stay CRLF and ASCII.
    """
    scripts = list(WINDOWS.glob("*.bat")) + list(WINDOWS.glob("*.ps1")) + list(WINDOWS.glob("*.vbs"))
    scripts.append(WINDOWS.parent.parent / "Install Golden Finds.bat")
    assert len(scripts) >= 6
    for script in scripts:
        data = script.read_bytes()
        assert data.isascii(), f"{script.name} has non-ASCII characters"
        assert b"\n" not in data.replace(b"\r\n", b""), f"{script.name} is not CRLF"
