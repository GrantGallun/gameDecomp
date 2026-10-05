# Tool-learning integration receipts

The laboratory is implemented; no model capability improvement is established.
All panels below are synthetic IDO smoke tests. Repeated templates are exposed,
not fresh holdouts. Target-generation compiles are separate common setup cost.

| Run | Author | Author calls | Scored compiles | Model tokens | Evaluation coverage | Lost / missing | Retained |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| v1 | qwen2.5-coder:14b | 1 | 32 | 1,241 | 3/12 -> 3/12 | 0 / 0 | no |
| v2 | qwen2.5-coder:14b | 3 | 38 | 5,903 | 3/12 -> 3/12 | 0 / 0 | no |
| v3 | qwen3:14b | 3 | 38 | 5,710 | 3/12 -> 3/12 | 0 / 0 | no |

Each run additionally compiled 14 synthetic target objects. No finished game
source was supplied. Model identities/digests and author outputs are retained.
v1 predates development feedback and sandbox hardening; v2 predates the final
malformed-response accounting fix. Their original manifests/receipts are
preserved rather than rewritten as evidence of the final code.

The model proposals failed protocol checks or failed to repair either motivating
development case. Their rationales included nonexistent C shift operators and
confusion between an observation object and a list of examples. The compiler and
retention gate rejected them. None produced a training example or production tool.

Final checks: **117 passed** across `test_tool_learning.py`, `test_coverage.py`,
`test_byte_certificate.py`, and `test_rewrites.py` in WSL. The new tests include
real filesystem/network/process isolation, scratch limits, malformed output,
timeouts, failed-case continuation, immutable targets, deadlines, source hashes,
quarantine, development-only feedback/export, and complete-cost accounting.
A developer-authored positive control runs the entire loop with real IDO,
certifies three evaluation objects and exports two development decisions. That
is a machinery check, not evidence of a model-authored improvement.

See [usage and limits](../../../docs/tool-learning.md). Actual run reports:
[v1](v1/experiment/report.json), [v2](v2/experiment/report.json),
[v3](v3/experiment/report.json).

## Refinement (2026-10-04, Claude)

Diagnosis of v1-v3 from their events: (1) the author was shown the development observations as a JSON LIST while
the sandbox feeds the tool ONE object, so both 14B authors wrote `for item in data` and emitted 0 candidates on
every case; (2) feedback reported only "ok, 0 candidates", with no compiler errors or diffs, so proposals 1 and 2
of v3 are identical; (3) reasoning was off (`think: False`), and `>>>` (not C) was never shown failing.
Changes: the prompt states the one-object contract with a code skeleton; development feedback carries each
candidate's compiler error or remaining diff and flags an empty tool; up to six author rounds; development rounds
compile only the tool's candidates; default author `gpt-oss:20b` with `think: low`; `--model lora:<adapter|base>`
authors with the 7B student on tools.lora_serve. Tests: 27 passed (2 new).

| Run | Author | Rounds | Dev shapes | Evaluation coverage | Lost | Tool cost (incl. dev) | Retained |
| --- | --- | ---: | --- | --- | --- | ---: | --- |
| v4 | gpt-oss:20b | 1 | 2 x `return v >> k;` | 3/12 -> 3/12 | 0 | 23 compiles | no |
| v5 | gpt-oss:20b | 3 | 4 shapes; revise until all dev repaired | 3/12 -> **8/12** | 0 | 53 compiles (baseline 15, budget 96) | **yes** |

v4 repaired both development cases on its first proposal (the 14B runs never repaired one) but froze a tool that
matched only `return v >> k;` and declined every evaluation case: the development set had one shape and the loop
stopped at the first success. v5 adds development shapes (no evaluation operator-with-constant reused) and keeps
revising until every development failure is repaired. Its tool casts the shifted operand to `unsigned int`
wherever `ident >> N` occurs; it gains eval0/1/2/4/5 and misses eval3 (variable shift count). Weaknesses: it does
not check the diff for the sra->srl evidence before rewriting, and its rationale calls the signed `sra` "incorrect".
4 development tool-use decisions were exported (not admitted to training).

Scope: v5's development shapes were chosen AFTER seeing v4 fail on this same exposed synthetic panel. This is an
integration result on known templates, not held-out evidence and not a game match.
