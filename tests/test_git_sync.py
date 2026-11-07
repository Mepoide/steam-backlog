import shutil
import subprocess

from git_sync import commit_and_push


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def setup_repo(tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.com")
    git(work, "checkout", "-q", "-b", "main")
    (work / "backlog.csv").write_text("appid\n1\n")
    git(work, "add", "backlog.csv")
    git(work, "commit", "-q", "-m", "inicial")
    git(work, "push", "-q", "-u", "origin", "main")
    return remote, work


def test_commit_and_push_creates_and_pushes_commit(tmp_path):
    remote, work = setup_repo(tmp_path)
    (work / "backlog.csv").write_text("appid\n1\n2\n")
    assert commit_and_push(["backlog.csv"], "backlog: sync", repo=work) is True
    assert git(remote, "log", "-1", "--format=%s", "main").strip() == "backlog: sync"


def test_no_changes_returns_true_without_commit(tmp_path):
    remote, work = setup_repo(tmp_path)
    before = git(work, "rev-parse", "HEAD")
    assert commit_and_push(["backlog.csv"], "nada", repo=work) is True
    assert git(work, "rev-parse", "HEAD") == before


def test_push_failure_returns_false_but_commits_locally(tmp_path):
    remote, work = setup_repo(tmp_path)
    shutil.rmtree(remote)
    (work / "backlog.csv").write_text("appid\n1\n3\n")
    assert commit_and_push(["backlog.csv"], "backlog: local", repo=work) is False
    assert git(work, "log", "-1", "--format=%s").strip() == "backlog: local"


def test_only_given_paths_are_committed(tmp_path):
    remote, work = setup_repo(tmp_path)
    (work / "backlog.csv").write_text("appid\n9\n")
    (work / "otro.txt").write_text("no")
    commit_and_push(["backlog.csv"], "solo backlog", repo=work)
    assert git(work, "show", "--name-only", "--format=", "HEAD").split() == ["backlog.csv"]


def other_clone_pushes(tmp_path, remote, filename, content, name="other"):
    other = tmp_path / name
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "Otro")
    git(other, "config", "user.email", "otro@example.com")
    (other / filename).write_text(content)
    git(other, "add", filename)
    git(other, "commit", "-q", "-m", f"cambio remoto en {filename}")
    git(other, "push", "-q")


def test_rebases_onto_newer_remote_before_push(tmp_path):
    remote, work = setup_repo(tmp_path)
    other_clone_pushes(tmp_path, remote, "README.md", "hola")
    (work / "backlog.csv").write_text("appid\n1\n2\n")
    assert commit_and_push(["backlog.csv"], "backlog: tras remoto", repo=work) is True
    log = git(remote, "log", "--format=%s", "main").split("\n")
    assert log[:2] == ["backlog: tras remoto", "cambio remoto en README.md"]


def test_conflict_aborts_rebase_and_returns_false(tmp_path):
    remote, work = setup_repo(tmp_path)
    other_clone_pushes(tmp_path, remote, "backlog.csv", "appid\nremoto\n")
    (work / "backlog.csv").write_text("appid\nlocal\n")
    assert commit_and_push(["backlog.csv"], "backlog: conflicto", repo=work) is False
    assert not (work / ".git" / "rebase-merge").exists() and not (work / ".git" / "rebase-apply").exists()
    assert git(work, "log", "-1", "--format=%s").strip() == "backlog: conflicto"
