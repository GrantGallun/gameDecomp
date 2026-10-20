"""Operator commands: check the setup, mine a knowledge base, create a campaign, run it, watch it.

    ./gd doctor                   what is installed and what is missing, with the fix for each
    ./gd kb                       mine the evidence knowledge base from the built target
    ./gd new NAME [--limit N | --functions F ...] [--model-calls N]
                                  freeze the code, create a campaign run, and make it current
    ./gd use RUN                  make an existing run directory current
    ./gd status | start | pause | resume | logs
                                  the campaign service, for the current run
    ./gd dashboard [--port N]     serve the progress dashboard for the current run (--stop stops it)

Everything runs inside WSL (or any Linux). Paths come from the environment; the defaults match the
layout in docs/RUNNING.md:

    GAMEDECOMP_HOME      ~/decomp                     knowledge bases, runs and worker slots
    GAMEDECOMP_TARGET    $GAMEDECOMP_HOME/sbk1        the built target decompilation
    GAMEDECOMP_RUN       $GAMEDECOMP_HOME/runs/CURRENT  the run the service commands act on
    GAMEDECOMP_ENDPOINT  the Windows host's Ollama, found as solver.llm.host() finds it

Starting, pausing and resuming go through eval/campaign_service.py, the same supervisor the hourly
maintenance task drives; it owns locks, retries and pause markers. This module adds nothing to them.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
# A run executes a frozen copy of these, so later edits to the tree cannot change a live campaign.
# frozen_wavefront.code_paths pins the first six; the rest are copied so the frozen tree is whole.
FROZEN = ("solver", "eval", "kb", "patterns", "tools", "miner", "oracle", "disasm", "tests")
SKIP = shutil.ignore_patterns("results", "__pycache__", ".cache", ".pytest_cache")
PACKAGES = ("rabbitizer", "yaml", "pycparser", "m2c", "spimdisasm", "splat", "pygfxd", "pytest")
# The target revision the pipeline is developed and tested on: upstream snowboardkids-decomp commit
# 7824002d (2026-08-22). The tree hash is what is checked, because upstream has since rewritten its
# history. Later revisions build into build/src.us/, which solver/compiler_recipe.py does not resolve,
# so every function parks at intake ("unsupported TU object identity"; fresh clone, 2026-10-19).
TESTED_TARGET = {"commit": "7824002d", "tree": "69e33c2fb375265424d98fd05e6f0a5a6d501e26"}


def home() -> Path:
    return Path(os.environ.get("GAMEDECOMP_HOME", "~/decomp")).expanduser()


def target() -> Path:
    return Path(os.environ.get("GAMEDECOMP_TARGET") or home() / "sbk1").expanduser()


def runs() -> Path:
    return home() / "runs"


def default_kb() -> Path:
    # Never the research KB's name: that file holds the project's measured history.
    return home() / f"kb-{target().name}-clean.sqlite"


def endpoint() -> str:
    if os.environ.get("GAMEDECOMP_ENDPOINT"):
        return os.environ["GAMEDECOMP_ENDPOINT"]
    from solver import llm
    return llm.host()


def current_run() -> Path:
    if os.environ.get("GAMEDECOMP_RUN"):
        return Path(os.environ["GAMEDECOMP_RUN"]).expanduser().resolve()
    pointer = runs() / "CURRENT"
    if pointer.is_file():
        return Path(pointer.read_text(encoding="utf-8").strip())
    raise SystemExit("no current run: create one with `./gd new NAME` or select one with `./gd use RUN`")


def set_current(run: Path) -> None:
    runs().mkdir(parents=True, exist_ok=True)
    (runs() / "CURRENT").write_text(str(run) + "\n", encoding="utf-8")


def counts(db: Path) -> dict:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("functions", "evidence", "attempts", "inference")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- doctor ------------------------------------------------------------------

def _built(repo: Path) -> tuple[bool, str]:
    """The target's own check: a built ROM matches the SHA-1 the decomp project records for it.

    A target may record several versions (snowboardkids.sha1 and snowboardkids.jp.sha1); the one
    that was built is the one that counts."""
    sums = sorted(repo.glob("*.sha1"))
    if not sums:
        return False, "no *.sha1 file in the target"
    built = []
    for record in sums:
        expected, _, rom = record.read_text().strip().partition("  ")
        rom = repo / rom.strip()
        if rom.is_file():
            built.append((record, rom, hashlib.sha1(rom.read_bytes()).hexdigest() == expected))
    if not built:
        return False, "no ROM built (" + ", ".join(record.name for record in sums) + ")"
    matched = [(record, rom) for record, rom, ok in built if ok]
    if not matched:
        return False, ", ".join(f"{rom.relative_to(repo)} does not match {record.name}"
                                for record, rom, _ in built)
    if len(list(repo.glob("build/*.elf"))) != 1 or len(list(repo.glob("build/*.map"))) != 1:
        return False, "build/ needs exactly one .elf and one .map"
    record, rom = matched[0]
    return True, f"{rom.relative_to(repo)} matches {record.name}"


def _revision(repo: Path) -> tuple[bool, str]:
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], capture_output=True, text=True)
    tree = done.stdout.strip() if done.returncode == 0 else ""
    if not tree:
        return False, "not a git checkout"
    if tree == TESTED_TARGET["tree"]:
        return True, f"the tested revision (upstream {TESTED_TARGET['commit']})"
    return False, f"tree {tree[:12]} is not the tested revision (upstream {TESTED_TARGET['commit']})"


def _model(url: str, model: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as response:
            names = {m.get("name") for m in json.load(response).get("models", [])}
    except (OSError, ValueError) as exc:
        return False, f"{url} not reachable ({type(exc).__name__})"
    return (model in names, f"{url} serves {model}" if model in names
            else f"{url} is up but has no {model}")


def checks(model: str = "gpt-oss:20b") -> list[dict]:
    """Every prerequisite, in setup order. `required` ones gate `./gd new` and `start`."""
    repo, kb = target(), Path(os.environ.get("GAMEDECOMP_KB") or default_kb()).expanduser()
    rows = []

    def row(name, ok, detail, fix, required=True):
        rows.append({"check": name, "ok": bool(ok), "detail": detail, "fix": fix, "required": required})

    row("linux", sys.platform.startswith("linux"), sys.platform,
        "run inside WSL2 Ubuntu; from Windows use `gd.cmd ...` or `wsl ./gd ...`")
    row("python", sys.version_info >= (3, 12), f"{sys.version.split()[0]} at {sys.executable}",
        "use Python 3.12, the version the suite and campaign run on")
    missing = [name for name in PACKAGES if importlib.util.find_spec(name) is None]
    row("packages", not missing, "missing: " + ", ".join(missing) if missing else "all importable",
        f"{sys.executable} -m pip install -r {ROOT / 'requirements.txt'}")
    tools = [t for t in ("mips-linux-gnu-objdump", "mips-linux-gnu-as") if not shutil.which(t)]
    row("binutils", not tools, "missing: " + ", ".join(tools) if tools else "mips-linux-gnu tools on PATH",
        "sudo apt install binutils-mips-linux-gnu")
    row("target", (repo / "Makefile").is_file(), str(repo),
        "clone the target decomp there (docs/RUNNING.md, step 2) or set GAMEDECOMP_TARGET")
    ok, detail = _revision(repo) if (repo / "Makefile").is_file() else (False, "no target")
    row("revision", ok, detail,
        f"cd {repo} && git checkout {TESTED_TARGET['commit']} && git -c url.\"https://github.com/\""
        f".insteadOf=\"git@github.com:\" submodule update --init --recursive, then rebuild (step 2)")
    row("ido", (repo / "tools/ido-recomp/linux/cc").is_file(), str(repo / "tools/ido-recomp/linux/cc"),
        f"make -C {repo / 'tools'}")
    ok, detail = _built(repo) if (repo / "Makefile").is_file() else (False, "no target")
    row("build", ok, detail,
        f"copy your ROM to {repo}/snowboardkids.z64, then: cd {repo} && . .venv/bin/activate && make extract && make")
    try:
        kb_detail = (", ".join(f"{k} {v:,}" for k, v in counts(kb).items()) if kb.is_file()
                     else f"{kb} absent")
    except sqlite3.Error as exc:
        kb_detail = f"{kb} unreadable: {exc}"
    row("kb", kb.is_file(), kb_detail, "./gd kb", required=False)
    url = endpoint()
    ok, detail = _model(url, model)
    row("model", ok, detail + " (only needed for --model-calls > 0)",
        f"start Ollama on Windows and run `ollama pull {model}`; or set GAMEDECOMP_ENDPOINT", required=False)
    return rows


def doctor(args) -> int:
    if not sys.platform.startswith("linux"):
        print("  MISS  linux     the pipeline runs inside WSL2 Ubuntu; from Windows use `gd.cmd doctor`")
        return 1
    rows = checks(args.model)
    for r in rows:
        mark = "ok  " if r["ok"] else ("MISS" if r["required"] else "--  ")
        print(f"  {mark}  {r['check']:<9} {r['detail']}")
        if not r["ok"]:
            print(f"        {'':<9} fix: {r['fix']}")
    try:
        run = current_run()
        print(f"\n  current run: {run}  ({_health(run).get('status')})")
    except SystemExit as exc:
        print(f"\n  {exc}")
    failed = [r["check"] for r in rows if r["required"] and not r["ok"]]
    print("\nready." if not failed else f"\nnot ready: {', '.join(failed)}")
    return 1 if failed else 0


# --- knowledge base ------------------------------------------------------------

def kb(args) -> int:
    out = Path(args.out or default_kb()).expanduser()
    if out.exists():
        raise SystemExit(f"{out} exists; the miner never replaces evidence. Pick another --out.")
    subprocess.run([sys.executable, "-m", "miner.evidence", "--repo", str(target()),
                    "--target", args.target_name or target().name, "--db", str(out)], cwd=ROOT, check=True)
    print(json.dumps({"kb": str(out), **counts(out)}))
    return 0


# --- new campaign --------------------------------------------------------------

def smallest(db: Path, limit: int) -> list[str]:
    """The `limit` smallest functions that are not in a frozen held-out set: a quick first cohort."""
    from eval import agentrepair
    chosen = []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        for (name,) in conn.execute("SELECT name FROM functions WHERE insn_count IS NOT NULL "
                                    "ORDER BY insn_count, name"):
            try:
                agentrepair._refuse_frozen_heldout(ROOT / "eval/sets", name)
            except ValueError:
                continue
            chosen.append(name)
            if len(chosen) == limit:
                break
    return chosen


def campaign_command(*, repo, db, state, code, model_calls, model, url, batch=10) -> list[str]:
    """The frozen worker command. The supervisor changes only --max-work-items and adds --resume."""
    return [sys.executable, "-m", "eval.completion_campaign", "--repo", str(repo), "--db", str(db),
            "--state", str(state), "--project", str(code), "--scheduler", "evidence-v1",
            "--max-work-items", str(batch), "--model-calls", str(model_calls), "--model", model,
            "--endpoint", url, "--timeout", "240", "--num-predict", "6000"]


def _git(*args) -> str:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def new(args) -> int:
    repo, db_source = target(), Path(args.kb or default_kb()).expanduser()
    run = (Path(args.name) if "/" in args.name else runs() / args.name).expanduser().resolve()
    if run.exists():
        raise SystemExit(f"{run} exists; a run is never reused. Pick another name.")
    ok, detail = _built(repo)
    if not ok:
        raise SystemExit(f"target not built ({detail}); see `./gd doctor`")
    tested, revision = _revision(repo)
    if not tested and not args.untested_target:
        raise SystemExit(f"target {revision}; see docs/RUNNING.md step 2, or pass --untested-target "
                         "to try it anyway")
    if not db_source.is_file():
        raise SystemExit(f"{db_source} absent; build it with `./gd kb`")
    history = counts(db_source)
    if (history["attempts"] or history["inference"]) and not args.allow_history:
        raise SystemExit(f"{db_source} already holds {history['attempts']:,} attempts and "
                         f"{history['inference']:,} inference rows, so a campaign on it is not fresh. "
                         "Use a clean KB (`./gd kb`) or pass --allow-history to say so explicitly.")
    url = args.endpoint or endpoint()
    if args.model_calls:
        ok, detail = _model(url, args.model)
        if not ok:
            raise SystemExit(f"--model-calls {args.model_calls} needs the model: {detail}")
    functions = args.functions or (smallest(db_source, args.limit) if args.limit else [])

    run.mkdir(parents=True)
    code, db, state = run / "code", run / "campaign.sqlite", run / "campaign.json"
    for name in FROZEN:
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, code / name, ignore=SKIP)
    shutil.copy2(ROOT / "pytest.ini", code / "pytest.ini")
    with sqlite3.connect(f"file:{db_source}?mode=ro", uri=True) as source, sqlite3.connect(db) as copy:
        source.backup(copy)
    command = campaign_command(repo=repo, db=db, state=state, code=code, model_calls=args.model_calls,
                               model=args.model, url=url)
    # Create the state without doing any work; the supervisor does the work, in batches.
    create = command.copy()
    create[create.index("--max-work-items") + 1] = "0"
    if functions:
        create += ["--functions", *functions]
    subprocess.run(create, cwd=code, check=True, stdout=subprocess.DEVNULL)
    from eval import campaign_state, frozen_wavefront
    nodes = json.loads(state.read_text())["nodes"]
    campaign_state.atomic(run / "launch.json", {
        "command": command, "created_at": time.time(), "created_by": "eval.control new",
        "regime": "header-assisted development; drafts may come from the target repo "
                  "(set GAMEDECOMP_ASSEMBLY_ONLY_DRAFTS=1 for capability numbers)",
        "target": str(repo), "target_revision": revision,
        "kb": str(db_source), "kb_sha256": sha256(db_source), "counts": history,
        "functions": len(nodes), "selection": "explicit" if args.functions else
        f"{args.limit} smallest" if args.limit else "entire inventory minus frozen held-out sets",
        "model": args.model, "model_calls": args.model_calls, "endpoint": url,
        # The commit the tree was on; code_hashes pins the exact content run, edits included.
        # (`git status` would say whether it was dirty, but from WSL over /mnt/c it takes a minute.)
        "git_commit": _git("rev-parse", "HEAD"),
        "code_hashes": frozen_wavefront.file_hashes(frozen_wavefront.code_paths(code)),
        "integration_requested": False})
    set_current(run)
    print(f"created {run}\n  {len(nodes)} functions, model calls per visit: {args.model_calls}\n"
          f"  it is now the current run. Start it with:  ./gd start")
    return 0


def use(args) -> int:
    run = Path(args.run).expanduser().resolve()
    if not (run / "launch.json").is_file():
        raise SystemExit(f"{run} has no launch.json, so it is not a campaign run")
    set_current(run)
    print(f"current run: {run}")
    return 0


# --- service -------------------------------------------------------------------

def _service(run: Path, action: str, batch: int = 10) -> dict:
    """eval/campaign_service.py from this tree, exactly as the hourly maintenance task calls it."""
    done = subprocess.run([sys.executable, str(ROOT / "eval/campaign_service.py"), "--run", str(run),
                           "--batch", str(batch), action], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(done.stderr.strip() or f"campaign_service {action} failed")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _health(run: Path) -> dict:
    spec = importlib.util.spec_from_file_location("campaign_service", ROOT / "eval/campaign_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.health(run)


def report(health: dict) -> None:
    states = health.get("states") or {}
    print(f"  status     {health.get('status')}")
    if health.get("functions") is not None:
        print(f"  functions  {health['functions']:,}: " +
              ", ".join(f"{name} {n:,}" for name, n in sorted(states.items(), key=lambda kv: -kv[1])))
    if health.get("checkpoint_age_seconds") is not None:
        print(f"  checkpoint written {health['checkpoint_age_seconds']:,} s ago")
    if health.get("error"):
        print(f"  error      {str(health['error']).strip().splitlines()[-1][:300]}")


def status(args) -> int:
    run = current_run()
    print(f"  run        {run}")
    report(_health(run))
    return 0


def start(args) -> int:
    run = current_run()
    health = _service(run, "ensure", args.batch)
    report(health)
    if health["status"] in {"paused", "pausing"}:
        print("  paused: `./gd resume` clears the pause and starts it")
    elif health["status"] == "needs_repair":
        print("  needs repair: read `./gd logs`, fix the cause, then `./gd resume` (it resets the retry budget)")
    return 0


def pause(args) -> int:
    report(_service(current_run(), "pause"))
    print("  the worker stops at the next work-item boundary; `./gd resume` continues")
    return 0


def resume(args) -> int:
    report(_service(current_run(), "resume", args.batch))
    return 0


def logs(args) -> int:
    run = current_run()
    for name in ("service.log", "pipeline.log"):
        path = run / name
        print(f"==> {path} <==")
        if path.is_file():
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 64 * 1024))
                print("\n".join(stream.read().decode("utf-8", "replace").splitlines()[-args.lines:]))
    return 0


def stop_dashboard(port: int) -> bool:
    """Stop the dashboard serving `port`, whether `./gd dashboard` or launch-progress.ps1 started it.

    Both start `python -m eval.progress_app ... --port N`, so the process is found by that command line."""
    done = subprocess.run(["pkill", "-f", rf"eval\.progress_app( .*)? --port {port}( |$)"])
    return done.returncode == 0


def dashboard(args) -> int:
    if args.stop:
        print(f"dashboard on port {args.port} stopped" if stop_dashboard(args.port)
              else f"no dashboard was running on port {args.port}")
        return 0
    run, url = current_run(), f"http://127.0.0.1:{args.port}"

    def up():
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                return json.load(response).get("app") == "gameDecomp-progress"
        except (OSError, ValueError):
            return False
    if not up():
        log = (run / "dashboard.log").open("a")
        process = subprocess.Popen([sys.executable, "-m", "eval.progress_app", "--run", str(run),
                                    "--port", str(args.port), "--wsl-python", sys.executable],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        (run / "dashboard.pid").write_text(f"{process.pid}\n")
        for _ in range(60):
            if up() or process.poll() is not None:
                break
            time.sleep(0.25)
        if not up():
            raise SystemExit(f"dashboard did not start; see {run / 'dashboard.log'}")
    print(f"dashboard: {url}  (WSL forwards 127.0.0.1, so open it in a Windows browser)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gd", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("doctor", help="check every prerequisite")
    p.add_argument("--model", default="gpt-oss:20b")
    p.set_defaults(func=doctor)
    p = sub.add_parser("kb", help="mine a fresh evidence knowledge base from the built target")
    p.add_argument("--out", help=f"default: {default_kb()}")
    p.add_argument("--target-name", help="label stored in the KB (default: the target directory name)")
    p.set_defaults(func=kb)
    p = sub.add_parser("new", help="freeze the code and create a campaign run")
    p.add_argument("name", help="run name under $GAMEDECOMP_HOME/runs, or a path")
    cohort = p.add_mutually_exclusive_group()
    cohort.add_argument("--functions", nargs="+", help="exactly these functions")
    cohort.add_argument("--limit", type=int, help="the N smallest functions (default: the whole game)")
    p.add_argument("--kb", help=f"knowledge base to copy (default: {default_kb()})")
    p.add_argument("--model-calls", type=int, default=0,
                   help="model calls per repair visit; 0 runs deterministic repair only (default)")
    p.add_argument("--model", default="gpt-oss:20b")
    p.add_argument("--endpoint", help="model server URL (default: $GAMEDECOMP_ENDPOINT or the Windows host)")
    p.add_argument("--allow-history", action="store_true",
                   help="accept a KB that already holds attempts (the run is then not fresh)")
    p.add_argument("--untested-target", action="store_true",
                   help=f"accept a target revision other than upstream {TESTED_TARGET['commit']}")
    p.set_defaults(func=new)
    p = sub.add_parser("use", help="make an existing run directory current")
    p.add_argument("run")
    p.set_defaults(func=use)
    for name, func, text in (("status", status, "health of the current run"),
                             ("start", start, "start the supervisor if it is stopped"),
                             ("pause", pause, "pause at the next work-item boundary"),
                             ("resume", resume, "clear a pause or exhausted retries, and start")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--batch", type=int, default=10, help="work items per worker process")
        p.set_defaults(func=func)
    p = sub.add_parser("logs", help="tail the service and pipeline logs")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.set_defaults(func=logs)
    p = sub.add_parser("dashboard", help="serve the progress dashboard for the current run")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--stop", action="store_true", help="stop the dashboard this command started")
    p.set_defaults(func=dashboard)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
