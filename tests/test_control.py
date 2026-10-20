"""`./gd` (eval/control.py): it creates runs the existing supervisor accepts, and refuses unsafe ones."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from eval import control

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A built target, a clean KB with four functions (one held out), and a small project tree."""
    target = tmp_path / "target"
    (target / "build").mkdir(parents=True)
    (target / "Makefile").write_text("all:\n")
    rom = b"rom bytes"
    (target / "build/game.z64").write_bytes(rom)
    (target / "game.sha1").write_text(f"{hashlib.sha1(rom).hexdigest()}  build/game.z64\n")
    (target / "build/game.elf").write_bytes(b"elf")
    (target / "build/game.map").write_text("map")

    kb = tmp_path / "kb.sqlite"
    with sqlite3.connect(kb) as conn:
        conn.executescript((ROOT / "kb/schema.sql").read_text())
        conn.executemany("INSERT INTO functions (addr, name, insn_count) VALUES (?, ?, ?)",
                         [(0x80001000, "tiny", 2), (0x80002000, "small", 5),
                          (0x80003000, "medium", 40), (0x80004000, "large", 400)])

    project = tmp_path / "project"
    for path, text in (("solver/a.py", "x = 1\n"), ("eval/b.py", "y = 2\n"),
                       ("eval/results/run/huge.json", "{}"), ("eval/__pycache__/b.pyc", ""),
                       ("eval/sets/held.json", json.dumps({"heldout": [{"function": "tiny"}]})),
                       ("pytest.ini", "[pytest]\n")):
        (project / path).parent.mkdir(parents=True, exist_ok=True)
        (project / path).write_text(text)
    monkeypatch.setattr(control, "ROOT", project)
    monkeypatch.setenv("GAMEDECOMP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAMEDECOMP_TARGET", str(target))
    monkeypatch.setenv("GAMEDECOMP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.delenv("GAMEDECOMP_RUN", raising=False)

    created = []

    class NoGit:
        returncode, stdout = 1, ""

    def run(command, **kwargs):
        """Stands in for `completion_campaign` creating its state; records the command it was given."""
        if "eval.completion_campaign" not in command:
            return NoGit()                     # provenance lookups: the project tree is not a git repo
        created.append((command, kwargs))
        state = Path(command[command.index("--state") + 1])
        names = command[command.index("--functions") + 1:] if "--functions" in command else ["all"]
        state.write_text(json.dumps({"nodes": {name: {"status": "pending"} for name in names}}))
    monkeypatch.setattr(control.subprocess, "run", run)
    monkeypatch.setattr(control, "_revision", lambda repo: (True, "the tested revision"))
    return {"tmp": tmp_path, "target": target, "kb": kb, "project": project, "created": created}


def test_new_creates_a_run_the_supervisor_accepts(setup):
    assert control.main(["new", "first", "--kb", str(setup["kb"]), "--limit", "2"]) == 0
    run = setup["tmp"] / "home/runs/first"

    # The state is created with no work done, for exactly the chosen cohort, from the frozen code.
    [(create, kwargs)] = setup["created"]
    assert create[create.index("--max-work-items") + 1] == "0"
    assert create[create.index("--functions") + 1:] == ["small", "medium"]   # `tiny` is held out
    assert kwargs["cwd"] == run / "code"

    launch = json.loads((run / "launch.json").read_text())
    command = launch["command"]
    assert command[command.index("--max-work-items") + 1] == "10" and "--functions" not in command
    assert command[command.index("--project") + 1] == str(run / "code")
    assert launch["functions"] == 2 and launch["model_calls"] == 0
    assert launch["counts"] == {"functions": 4, "evidence": 0, "attempts": 0, "inference": 0}

    # Frozen code: sources copied, run data and caches left behind.
    assert (run / "code/solver/a.py").is_file() and (run / "code/pytest.ini").is_file()
    assert not (run / "code/eval/results").exists() and not (run / "code/eval/__pycache__").exists()
    assert control.counts(run / "campaign.sqlite")["functions"] == 4
    assert control.current_run() == run


def test_the_supervisor_reads_the_run_new_writes(setup):
    """The contract with eval/campaign_service.py: its command rewrite and state lookup."""
    pytest.importorskip("fcntl")
    control.main(["new", "first", "--kb", str(setup["kb"])])
    run = setup["tmp"] / "home/runs/first"
    spec = importlib.util.spec_from_file_location("campaign_service", ROOT / "eval/campaign_service.py")
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    command = service.command_for(run, 7)
    assert command[command.index("--max-work-items") + 1] == "7" and command[-1] == "--resume"
    assert service.state_path_for(run) == run / "campaign.json"


def test_new_refuses_a_kb_that_already_has_history(setup):
    with sqlite3.connect(setup["kb"]) as conn:
        conn.execute("INSERT INTO inference (kind, subject, value, origin, created_at) "
                     "VALUES ('field', 'struct:A@0x0', '{}', 'human', 0)")
    with pytest.raises(SystemExit, match="not fresh"):
        control.main(["new", "first", "--kb", str(setup["kb"])])
    assert not (setup["tmp"] / "home/runs/first").exists()
    assert control.main(["new", "first", "--kb", str(setup["kb"]), "--allow-history"]) == 0


def test_new_refuses_an_unbuilt_target_and_an_existing_run(setup):
    (setup["target"] / "build/game.z64").write_bytes(b"different")
    with pytest.raises(SystemExit, match="not built"):
        control.main(["new", "first", "--kb", str(setup["kb"])])
    (setup["target"] / "build/game.z64").write_bytes(b"rom bytes")
    control.main(["new", "first", "--kb", str(setup["kb"])])
    with pytest.raises(SystemExit, match="never reused"):
        control.main(["new", "first", "--kb", str(setup["kb"])])


def test_new_refuses_a_target_revision_the_pipeline_was_not_tested_on(setup, monkeypatch):
    """Measured 2026-10-19: on upstream's later layout (build/src.us/) every function parked at intake."""
    monkeypatch.setattr(control, "_revision", lambda repo: (False, "tree 0123456789ab is not the tested revision"))
    with pytest.raises(SystemExit, match="not the tested revision"):
        control.main(["new", "first", "--kb", str(setup["kb"])])
    assert not (setup["tmp"] / "home/runs/first").exists()
    assert control.main(["new", "first", "--kb", str(setup["kb"]), "--untested-target"]) == 0
    launch = json.loads((setup["tmp"] / "home/runs/first/launch.json").read_text())
    assert launch["target_revision"] == "tree 0123456789ab is not the tested revision"


def test_revision_is_judged_by_tree_not_commit(tmp_path):
    """Upstream rewrote its history, so the tested content is identified by its tree hash."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True)
    (repo / "f").write_text("x")
    subprocess.run(git + ["add", "f"], check=True)
    subprocess.run(git + ["commit", "-qm", "x"], check=True)
    tree = subprocess.run(git + ["rev-parse", "HEAD^{tree}"], capture_output=True, text=True).stdout.strip()
    ok, detail = control._revision(repo)
    assert not ok and detail.startswith(f"tree {tree[:12]}")
    original = dict(control.TESTED_TARGET)
    try:
        control.TESTED_TARGET["tree"] = tree
        assert control._revision(repo)[0]
    finally:
        control.TESTED_TARGET.update(original)


def test_a_second_unbuilt_version_does_not_hide_the_built_one(setup):
    """The upstream decomp records a JP build too; its .sha1 sorts first and was never built."""
    (setup["target"] / "game.jp.sha1").write_text("0" * 40 + "  build/jp/game.z64\n")
    assert control._built(setup["target"]) == (True, "build/game.z64 matches game.sha1")
    (setup["target"] / "build/game.z64").write_bytes(b"different")
    ok, detail = control._built(setup["target"])
    assert not ok and detail == "build/game.z64 does not match game.sha1"


def test_doctor_names_each_missing_prerequisite_and_its_fix(setup, monkeypatch):
    monkeypatch.setenv("GAMEDECOMP_TARGET", str(setup["tmp"] / "nowhere"))
    rows = {r["check"]: r for r in control.checks()}
    for name in ("target", "ido", "build"):
        assert not rows[name]["ok"] and rows[name]["required"] and rows[name]["fix"]
    assert "make -C" in rows["ido"]["fix"]
    assert not rows["model"]["ok"] and not rows["model"]["required"]    # optional: deterministic runs


@pytest.mark.parametrize("arguments", [["--port", "8791"],                         # launch-progress.ps1
                                       ["--run", "/r", "--port", "8791", "--wsl-python", "p"]])  # ./gd dashboard
def test_dashboard_stop_ends_a_dashboard_however_it_was_started(capsys, monkeypatch, arguments):
    """Measured 2026-10-20: a dashboard from launch-progress.cmd had no way to be stopped, and
    `--stop` only knew the pid file of one `./gd dashboard` had written. Neither needs a current run."""
    pytest.importorskip("fcntl")                 # pkill and POSIX signals; the dashboard is served from WSL
    import subprocess
    import sys
    monkeypatch.delenv("GAMEDECOMP_RUN", raising=False)
    monkeypatch.setenv("GAMEDECOMP_HOME", "/nonexistent")
    # Same command-line shape as the real server: `... eval.progress_app <arguments>`.
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "eval.progress_app", *arguments])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "eval.progress_app", "--port", "87910"])
    try:
        assert control.main(["dashboard", "--stop", "--port", "8791"]) == 0
        assert process.wait(timeout=10) != 0
        assert other.poll() is None                  # another port's dashboard is left alone
        assert "stopped" in capsys.readouterr().out
        control.main(["dashboard", "--stop", "--port", "8791"])
        assert "no dashboard was running" in capsys.readouterr().out
    finally:
        other.kill()


def test_start_drives_the_supervisor_the_way_the_hourly_task_does(setup, monkeypatch):
    run = setup["tmp"] / "run"
    monkeypatch.setenv("GAMEDECOMP_RUN", str(run))
    calls = []

    class Done:
        returncode, stderr = 0, ""
        stdout = json.dumps({"status": "starting"}) + "\n"
    monkeypatch.setattr(control.subprocess, "run", lambda argv, **kw: calls.append(argv) or Done())
    assert control.main(["start", "--batch", "5"]) == 0
    [argv] = calls
    assert argv[1] == str(control.ROOT / "eval/campaign_service.py")
    assert argv[2:] == ["--run", str(run.resolve()), "--batch", "5", "ensure"]
