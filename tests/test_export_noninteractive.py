import os
import shutil
import subprocess
import sys
from pathlib import Path

EXPORT = Path(__file__).resolve().parent.parent / "src" / "export.py"


def run_export(tmp_path, extra_env=None):
    # Copia aislada: ROOT apunta a tmp_path, así no se lee el .env real ni se toca la red.
    (tmp_path / "src").mkdir()
    shutil.copy(EXPORT, tmp_path / "src" / "export.py")
    env = {"PATH": os.environ["PATH"], **(extra_env or {})}
    return subprocess.run([sys.executable, str(tmp_path / "src" / "export.py")], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)


def test_export_noninteractive_missing_key_exits(tmp_path):
    r = run_export(tmp_path)
    assert r.returncode != 0
    assert "Falta STEAM_API_KEY en .env" in r.stderr


def test_export_noninteractive_missing_steam_id_exits(tmp_path):
    r = run_export(tmp_path, {"STEAM_API_KEY": "x"})
    assert r.returncode != 0
    assert "Falta STEAM_ID en .env" in r.stderr
