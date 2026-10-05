# Round 3: the fix is staged, and the campaign is stopped by the filesystem

**Date:** 2026-09-19 · byte-exact unchanged at 333

## Done: the round-2 fix is now inside the run

`eval/amend_run.py --revision amend4-20260919` staged **4 code files** (`solver/compile_recovery.py`
and its tests — the placeholder stage added in round 2), with `pins_added 0 / removed 0 / changed 0`,
`model_identical: true`, round-trip verified, 2,051 nodes preserved. The revision is kept at
`revisions/amend4-20260919/`.

## Blocked: `os.replace` on `/mnt/c` fails inside the campaign process

The worker starts, then dies on every attempt:

```
PermissionError: [Errno 13] Permission denied:
  '.../resume-pipeline-20260908/.campaign.json.new' -> '.../resume-pipeline-20260908/campaign.json'
```

Reproduced twice, with a clean lock and a verified-writable directory between attempts. Two facts
narrow it:

| test | result |
|---|---|
| `mv .probe probe-target` inside the run directory | **OK** — rename to a new name works |
| `cp campaign.json .cp-probe && mv .cp-probe campaign.json.probe` | **OK** — replacing an existing file works *from a shell* |
| the campaign's own `os.replace('.campaign.json.new', 'campaign.json')` | **PermissionError**, every attempt |
| the supervisor's `os.replace('.service.json.service.tmp', 'service.json')` | **PermissionError**, same directory, earlier round |

So it is not a transient lock and not a permissions problem — it is **one process replacing a file it
itself has open**, which `/mnt/c` (DrvFs) refuses and a POSIX filesystem does not. `CLAUDE.md` states
the rule this run violates:

> Keep the build tree on the **WSL filesystem** (`~/`), not `/mnt/c`. Cross-filesystem I/O is slow
> enough to break the Oracle's latency target, and the Oracle runs millions of times.

The run predates that being enforced for its own state files, and it made 1,150 batches before the
state grew to ~37 GB (`campaign.sqlite` 7.6 GB, `campaign.state.sqlite` 11.6 GB, a 17.7 GB pre-prune
snapshot left on disk). The larger the directory, the more Windows-side handles there are on it.

## The fix, and why it is not a code change

Move the run to the WSL filesystem. That is a ~37 GB copy and a path rewrite in `launch.json`,
`service.json` and the state's `config.project`/`config.db` — mechanical but large, and it deserves
its own round with verification rather than being bolted onto this one. The pre-prune snapshot is
disposable and roughly halves the copy.

## Honest position

- The campaign is **not running**. It has made no progress this round, and the 987-function
  `compiles-not-exact` class and the ≥1 KiB wall are both untouched.
- What round 3 produced is a verified amendment plus a precisely localised wall with a reproduction
  table — not a match.
- Round 2's fix is staged and will take effect on the first successful start after the move; it has
  never executed inside the campaign.
