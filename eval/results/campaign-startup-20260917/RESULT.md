# Why the campaign would not start, and what it takes to make the treemap move

**Date:** 2026-09-17 · model calls 0 · byte-exact unchanged at 333

## What the dashboard was showing

`eval/results/resume-pipeline-20260908/service.json`:

```
status               : needs_repair
returncode           : 1
consecutive_failures : 3
heartbeat_at         : 25 hours old
```

The supervisor kept restarting `eval.fast_campaign` and it kept dying at the same line:

```
frozen_wavefront.py:86  urllib.request.urlopen(endpoint + "/api/tags", timeout=10)
fast_campaign.py:243    if frozen_wavefront.model_digest(args.endpoint, args.model) != state['model_digest']
URLError: <urlopen error timed out>
```

`launch.json` records what it was pinned to: `endpoint http://172.28.32.1:11435`, `model gpt-oss:20b`,
`model_digest 17052f91…`. Nothing is listening there, so the freeze check could not complete and the
controller never reached any work.

## The guard, and why it is the existing convention rather than an exemption

`model_digest` is a freeze check: it proves the endpoint still serves the model the run was pinned to,
so a resumed campaign cannot silently drift onto a different one. That check has no meaning when the
run makes no model calls — and `run_expansion.py:202` already guards the identical call:

```python
model_identity = frozen_wavefront.model_digest(llm.host(), 'gpt-oss:20b') if args.model_calls else None
```

`fast_campaign.py:243` now does the same. `tests/test_fast_campaign_model_freeze.py` (3 tests) pins
both halves: a zero-model run must not touch the endpoint at all, and a model run must still refuse a
changed model. That second test is the one that matters — the guard must not weaken the invariant it
was protecting.

## What this does NOT do — and where my earlier estimate was wrong

I said this was "~an hour". It is not, and the reason is architecture I had not read when I said it:

- **There is no fresh-run bootstrap.** `fast_campaign` requires `--resume` and validates the recorded
  configuration exactly. `campaign_state` exposes `read`, `read_for_resume` and `Store.save` — nothing
  that creates a run. The initial state (2051 nodes, pins, `inventory_sha256`, `model_digest`,
  `config`) is written by something bespoke I did not find.
- **The existing run cannot be resumed with `--model-calls 0` either.** `read_for_resume` compares
  `state['config']` against the CLI arguments field by field, including `model_calls`, and that run
  recorded `3`. A resumption with `0` raises `resume configuration differs` before the guard is
  reached.
- **Code fixes reach that run by amendment, not by deployment.** `eval/results/*/deploy.py` stages the
  changed files with old/new SHA256 pairs and a passing test log, then copies them into `RUN/code/`
  under a lock, requiring the run to be paused and drained and **requiring the model digest to match**
  (`deploy.py:37`). So the amendment path is blocked by the same dead endpoint the guard just unblocked
  in the other controller.
- **The campaign is one long-lived run that accumulates revisions.** `RUN/code/` is the code that
  actually executes; the tree I have been editing is the *source* of later amendments, not what the run
  reads. That also means none of this session's fixes have ever been exercised by the campaign.

## The shortest honest path to a moving treemap

1. Stand up an endpoint that satisfies the pin — `gpt-oss:20b` at `172.28.32.1:11435` with the
   recorded digest. Then the existing run resumes, and it resumes **without** this session's fixes.
2. Or write a run bootstrap: construct the initial state for a new run directory with
   `model_calls: 0`, and amend this session's changed files into its `code/`. That is the only path
   that both moves the map and runs the fixed pipeline. It needs the initial-state schema, which is
   the piece I have not found.

## Suite

**3358 passed, 3 skipped, 1 failed** — the failure is the pre-existing environmental
`test_project64_trace::test_multi_job_validation_accepts_and_rejects`.
