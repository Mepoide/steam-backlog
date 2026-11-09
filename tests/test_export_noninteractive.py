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


def _export_module(monkeypatch):
    import importlib
    monkeypatch.setitem(sys.modules, "dotenv", None)  # que no cargue el .env real
    monkeypatch.syspath_prepend(str(EXPORT.parent))
    return importlib.import_module("export")


def test_keep_previous_restores_shared_games_and_dates(monkeypatch, tmp_path):
    export = _export_module(monkeypatch)
    lib = {"10": {"appid": 10, "name": "Propio", "source": "propio", "acquired": "", "owners": "7"}}
    previous = [
        {"appid": "10", "name": "Propio", "source": "propio", "acquired": "2022-11-09", "owners": "7;8"},
        {"appid": "20", "name": "Familiar", "source": "compartido", "playtime_hours": "3.5",
         "acquired": "2024-01-01", "owners": "8"},
    ]
    assert export.keep_previous(lib, previous) == 1
    assert lib["10"]["acquired"] == "2022-11-09" and lib["10"]["owners"] == "7"  # no pisa lo nuevo
    assert lib["20"]["source"] == "compartido" and lib["20"]["acquired"] == "2024-01-01"
    assert export.read_previous(tmp_path / "no.csv") == []
