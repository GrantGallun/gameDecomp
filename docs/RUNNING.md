# Running gameDecomp

This page takes you from a clean Windows machine to a running campaign, then covers the day-to-day
commands. Steps 2 to 4 and every `./gd` command were run end to end on 2026-10-19 against a fresh
clone of the target, in Ubuntu 24.04 on WSL2, using this repository's working tree rather than a
fresh clone of it. The times quoted come from that run. Where a step can fail, the fix is listed next
to it.

Everything except the model server and your browser runs inside **WSL2 Ubuntu**:

| Where | What |
|---|---|
| WSL2 Ubuntu | the target decomp and its IDO toolchain, this repository's pipeline, the campaign, the dashboard server |
| Windows | Ollama (optional, for model-assisted repair) and the browser you open the dashboard in |

The commands are one script, `./gd`, run from this repository's root inside WSL. From a Windows prompt
in the same directory, `gd.cmd <command>` runs the same thing through WSL. `./gd --help` lists the
commands.

## One-time setup

### 1. WSL2 and system packages

Install WSL2 with Ubuntu 24.04 (`wsl --install -d Ubuntu-24.04` in an administrator PowerShell). Then,
inside Ubuntu:

```sh
sudo apt update
sudo apt install make git python3 python3-venv python3-pip binutils-mips-linux-gnu wget unzip bubblewrap
```

`bubblewrap` is the sandbox `eval/tool_sandbox.py` runs model-written tools in; without it they are
refused, never run unsandboxed.

Keep the target and knowledge bases on the WSL filesystem (`~/...`), not under `/mnt/c`. Builds and
SQLite writes across that boundary are slow enough to matter.

### 2. The target decompilation

The first target is Snowboard Kids, whose community decompilation provides the build, the IDO 5.3
toolchain (`ido-static-recomp`) and the reference to check against. You need your own copy of the
game. No ROM is included here.

```sh
mkdir -p ~/decomp && cd ~/decomp
git -c url."https://github.com/".insteadOf="git@github.com:" \
    clone https://github.com/cdlewis/snowboardkids-decomp.git sbk1
cd sbk1
git checkout 7824002d                           # the revision the pipeline is tested on, see below
git -c url."https://github.com/".insteadOf="git@github.com:" submodule update --init --recursive
make -C tools                                   # downloads the IDO 5.3 recompilation
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r requirements.txt
cp /path/to/your/rom.z64 snowboardkids.z64      # big-endian .z64
make extract && make -j"$(nproc)"
```

The build ends with `build/snowboardkids.z64: OK`. It took under a minute, and the tree is about
300 MB. Two details matter:

- **The pinned revision.** Upstream later split the build by region (`build/src.us/...`, for its
  Japanese build). The pipeline resolves only `build/src/...`, so on a newer checkout every function
  parks at intake with `unsupported TU object identity`. `./gd doctor` checks the revision by tree
  hash, because upstream has rewritten its history, and `./gd new` refuses any other revision unless
  you pass `--untested-target`.
- **`insteadOf`.** One of the decomp's submodules is listed by its SSH address. Without a GitHub SSH
  key, fetching it fails at `tools/asm-differ`.

### 3. This repository

```sh
cd ~ && git clone https://github.com/GrantGallun/gameDecomp.git && cd gameDecomp
~/decomp/sbk1/.venv/bin/python -m pip install -r requirements.txt
./gd doctor
```

The pipeline shares the decomp's virtual environment: its `make` targets and this pipeline need the
same splat, m2c and pycparser versions, and `requirements.txt` pins exactly those. `./gd` finds that
interpreter by itself. `./gd doctor` checks every prerequisite and prints the fix for anything missing.
`ready.` means steps 1 to 3 are complete.

### 4. A knowledge base

```sh
./gd kb
```

This mines the evidence tier (memory accesses, calls, data references) from the built ELF into
`~/decomp/kb-sbk1-clean.sqlite`. It takes a few seconds and finds 2,113 functions and 72,041 evidence
rows. It refuses to overwrite an existing database, so earlier results are never replaced.

### 5. A model server (optional)

Campaigns run deterministic repair only unless you ask for model calls. To enable the model-assisted
stage, install [Ollama](https://ollama.com) on Windows and pull the model:

```powershell
ollama pull gpt-oss:20b
```

WSL reaches Windows through its default gateway, and `./gd` finds that address by itself. If Ollama
is not reachable from WSL, set `OLLAMA_HOST=0.0.0.0` for the Ollama server, or point `./gd` somewhere
else with `GAMEDECOMP_ENDPOINT=http://host:port`. `launch-campaign-gpu.ps1` starts a second, dedicated
Ollama on port 11435 with the settings the long-running campaign used. `./gd doctor` shows whether the
model is reachable.

## Turn it on

```sh
./gd new first --limit 20        # a run over the 20 smallest functions; omit --limit for the whole game
./gd start                       # start the supervisor
./gd status                      # progress
./gd dashboard                   # http://127.0.0.1:8765, open it in a Windows browser
```

`new` freezes a copy of the code into the run, so later edits to this repository cannot change a
running campaign. It copies the knowledge base and creates the campaign state, then makes the new run
current. Every other command acts on the current run. It also removes the upstream helper's refusal
of `do` loops from `tools/claude-decomp-env/build.sh` in the target, so `git status` there shows that
file modified. The refusal is a policy, not a compiler limit (see `eval/remove_do_ban.py`). Add
`--model-calls 3` to `new` to allow up to three model calls per repair visit.

| Command | What it does |
|---|---|
| `./gd status` | the supervisor's state, functions by status, checkpoint age |
| `./gd logs` | the end of the service and pipeline logs |
| `./gd pause` | stops at the next work-item boundary. The pause survives restarts |
| `./gd resume` | clears a pause, or resets the retry budget after `needs_repair`, and starts |
| `./gd start` | starts the supervisor if it is stopped. It never clears a pause |
| `./gd dashboard --stop` | stops the dashboard on its port (8765 unless `--port`), whether `./gd dashboard` or `launch-progress.cmd` started it |
| `./gd use RUN` | makes another run directory current |

The supervisor (`eval/campaign_service.py`) runs the campaign in batches of ten work items, each in a
fresh process, and saves after every item. A worker that fails three times in a row stops the run as
`needs_repair` rather than looping. Read `./gd logs`, fix the cause, then `./gd resume`. If the target
repository or the frozen code changes underneath a run, it stops as `needs_repair` with
`paused_inputs_changed`. That run is pinned to its inputs, so create a new one.

The supervisor keeps running after the terminal that started it closes. To turn everything off and
give WSL's memory back to Windows, run `wsl --shutdown` from Windows. That, or a Windows restart, stops
the campaign, and `./gd start` continues it from the last saved work item. The project's
long-running campaign is restarted by an hourly Windows task (`eval/campaign_hourly.ps1`, written for
that one run).

To operate a run that was not created by `./gd new`, such as that long-running campaign, select it
first: `./gd use /mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908`.

### What a run contains

`~/decomp/runs/<name>/`:

| Path | |
|---|---|
| `launch.json` | the frozen worker command, plus provenance: KB hash, git commit, code hashes |
| `code/` | the frozen copy of the pipeline the campaign executes |
| `campaign.sqlite` | the run's knowledge base. Every attempt, including failures, is logged here |
| `campaign.json` | the campaign state, saved after every work item |
| `campaign-artifacts/` | one receipt per work item |
| `service.json`, `pipeline.log`, `service.log` | the supervisor's record and logs |

## Measuring capability

Drafts may start from files the target decomp ships (`nonmatchings/<fn>/base.c`), which carry the
decomp team's types. For any number meant to describe the pipeline's own capability, set
`GAMEDECOMP_ASSEMBLY_ONLY_DRAFTS=1` in the environment the campaign runs in. See
`eval/results/intake-20260921/CONTAMINATION.md` and "Evaluation hygiene" in `CLAUDE.md`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| clone stops at `tools/asm-differ` | use the `insteadOf` setting in step 2 |
| every function parks at intake with `unsupported TU object identity` | the target is a newer upstream revision. Check out `7824002d` (step 2) and rebuild |
| `Wsl/Service/CreateInstance/CreateVm/HCS/ERROR_NO_SYSTEM_RESOURCES` | Windows has too little free memory to start WSL. Close something large, or lower `memory=` in `%USERPROFILE%\.wslconfig` |
| `./gd doctor` shows `model ... not reachable` | Ollama is not running, or listens on 127.0.0.1 only (see step 5) |
| `needs_repair` | `./gd logs`, fix the cause, `./gd resume` |
| `paused_inputs_changed` | the target or frozen code changed. Start a new run with `./gd new` |
