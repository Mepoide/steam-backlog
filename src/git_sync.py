#!/usr/bin/env python3
"""Commit + push de backlog.csv (job diario y /marcar del bot).

    python src/git_sync.py "mensaje"
"""
import fcntl
import logging
import subprocess
import sys
from pathlib import Path

from config import ROOT

log = logging.getLogger(__name__)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


def commit_and_push(paths: list[str], message: str, repo: Path = ROOT) -> bool:
    repo = Path(repo)
    lock_path = repo / "data" / ".git.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _git(repo, "add", "--", *paths)
            if _git(repo, "diff", "--cached", "--quiet", "--", *paths, check=False).returncode == 0:
                return True
            _git(repo, "commit", "-q", "-m", message, "--", *paths)
        except subprocess.CalledProcessError as e:
            log.error("git commit falló: %s", e.stderr.strip())
            return False
        pull = _git(repo, "pull", "-q", "--rebase", "--autostash", check=False)
        if pull.returncode != 0:
            _git(repo, "rebase", "--abort", check=False)
            log.error("git pull --rebase falló (el commit queda en local): %s", pull.stderr.strip())
            return False
        push = _git(repo, "push", "-q", check=False)
        if push.returncode != 0:
            log.error("git push falló (el commit queda en local): %s", push.stderr.strip())
            return False
        return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    msg = sys.argv[1] if len(sys.argv) > 1 else "backlog: actualización"
    ok = commit_and_push(["backlog.csv"], msg)
    print("✓ backlog sincronizado con git" if ok else "⚠ no se pudo sincronizar con git (ver log)")
    sys.exit(0 if ok else 1)
