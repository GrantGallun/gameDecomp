# Campaign operations

Current run: `/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908`.
WSL distribution: Ubuntu. Python: `/home/grant/decomp/sbk1/.venv/bin/python`.

September 9 performance amendment: checkpoint and supervisor writes now use
compact JSON and a 1 MiB buffer. Every work item is still saved. The original
checkpoint and writer are archived in `revisions/20260909-buffered-checkpoint`.
Measured save time fell from 15.111 s to 3.153 s on identical data; file size
fell from 276.6 MB to 133.0 MB. Forty-six focused tests passed. Use JSON parsing
tools for compact files rather than assuming one key per text line.

`eval/campaign_service.py --run <run> ensure` starts a missing supervisor.
`check` reports health from live nodes, not the potentially stale summary.
`pause` prevents subsequent workers and requests a pause at a work-item boundary.
`resume` clears the user pause and explicitly resets an exhausted retry budget.
Do not call resume automatically after unknown failures without diagnosing them.

The supervisor uses the existing frozen command with `--resume`, changing only
`--max-work-items` to 10 per process. Completed work is checkpointed per item;
process replacement releases accumulated memory. It saves a validated rolling
`checkpoint.previous.json` before each batch. Existing per-item receipts allow
interrupted completed work to be replayed without rerunning it. Work interrupted
before its receipt was committed can repeat, including model calls; this is not
an exactly-once guarantee. A corrupt checkpoint is never silently rolled back.

There is at most one supervisor and one campaign worker, enforced with existing
locks. Nonzero worker exits get two retries, then `needs_repair`. Frozen-input
changes stop immediately. Successful budget completion starts the next batch.
`service.json`, `service-events.jsonl`, `health.json`, and `pipeline.log` record
processes, outcomes and recovery. A fresh heartbeat alone is not proof that a
work item is advancing; inspect child activity and checkpoint age for stalls.

Windows task `GameDecomp-Hourly-Campaign-Maintenance` runs every hour and at user
logon. It calls `campaign_hourly.ps1`, checks/resumes known stopped service state,
and invokes authenticated `codex exec --sandbox workspace-write` only for
`needs_repair`. It does not bypass permissions or automatically approve actions.
Repair may remain blocked by sandbox access or model availability. Findings live
under `monitor/`; these are local reports, not notifications in the original chat.
It runs while this Windows user is logged in; missed checks run when available.
Overlapping scheduled runs are disabled. The task has a 55-minute runtime limit.
The script selects the newest bundled desktop Codex CLI; the globally installed
0.144.3 CLI failed model compatibility, while the bundled 0.153.4 passed the
authentication smoke test. No permission bypass flags are used.

Installation validation: Windows task returned exit code 0 and recorded a paused
health result; 35 focused service/campaign/checkpoint tests passed, including real
child-process crash recovery and exhaustion after three failures. The final
broader test invocation encountered WSL connection error 0x8007274c; it did not
produce a test result. Actual supervisor workers subsequently advanced in the log.

Host `.wslconfig` was backed up to `wslconfig.before` and changed from 2 GB to
8 GB RAM, preserving four CPUs and 8 GB swap. Activation requires a WSL VM
restart. Docker Desktop was running, so restart approval is pending; do not
claim the increased allocation is active before verifying it inside WSL.

Pause before gaming or maintenance using the service command. Suspension by
SIGSTOP retains memory and is different from the durable pause control. Do not
restart paused processes behind the user's back. Use the service pause marker
when changing WSL settings so the hourly task cannot restart during maintenance.

The original campaign snapshot has explicit recorded runtime amendments under
`revisions/`: float32 overflow becomes unsupported/inconclusive, and checkpoint
JSON is streamed atomically. Future amendments require a stopped worker, archived
checkpoint and old code, focused tests, before/after hashes, and verification of
every unchanged input pin. Do not repin unrelated code or source inputs. Preserve
all previous results; a runtime repair is not a new clean benchmark.
