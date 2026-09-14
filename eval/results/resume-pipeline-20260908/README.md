# Current game-wide pipeline replay

Started September 8, 2026, at the user's request to retry the current pipeline and run as long as needed.

- Entire 2,113-function inventory was considered: **2,051 eligible functions**, with **62 frozen held-out functions excluded** by the controller.
- Current code is frozen in `code/`, with hashes in `launch.json`.
- Uses the ROM-range-validated baseline (72,041 evidence records, zero initial attempts), copied into `campaign.sqlite`.
- Uses the current `evidence-v1` scheduler and local `gpt-oss:20b`, with up to three calls per model-enabled work item. The per-call timeout is 240 seconds and the output limit is 6,000 tokens.
- Runs the actual intake, context adaptation, deterministic repair, semantic checks, and model-assisted repair stages. Existing bootstrap drafts are available; historical repaired-candidate seeds are not supplied. This is a header-assisted development replay, not an unseen/binary-only benchmark.
- No wall-clock limit. The launcher automatically resumes after each 1,000-work-item budget. It stops on a terminal/stalled status, changed frozen inputs, or a worker error; exhaustion is not proof that unsolved functions are impossible.
- No integration into game source is requested. Exact function/object results remain distinct from a verified whole-ROM rebuild.

## Live receipts

- [Launch manifest](launch.json): configuration, model digest, code hashes, process IDs, and eventual terminal summary.
- [Campaign checkpoint](campaign.json): authoritative per-function state, exclusions, completed jobs, exactness, and inflight work. During a work item, aggregate summaries may lag; use the nodes and inflight fields.
- [Pipeline log](pipeline.log): completed work items and errors.
- `campaign-artifacts/`: source-bound compiler/semantic/model receipts.
- Original Codex execution session: `96276`. Launcher starts a separate session for its WSL worker. Keep the computer awake for progress; shutdown interrupts execution, with checkpoints retained.

The earlier `../resume-20260908/` census recompiled saved candidates. Its results must not be mixed into this run's numerator. New resume claims should use completed receipts from this run, with its actual denominator and remaining unvisited/blocked functions.

For a manual resume after an interruption, first confirm the worker and launcher recorded in `launch.json` are no longer running. Use the exact saved `command` array from that manifest with `--resume`, and run from the frozen `code/` directory. Do not rerun the setup launcher into the existing directory; it refuses overwrites.
