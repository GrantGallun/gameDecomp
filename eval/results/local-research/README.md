# Local research setup verification — 2026-09-20

The dashboard is at `http://127.0.0.1:8765`. The research worker was left **stopped**.
Usage: `docs/local-research.md`. Training handoff:
`docs/deepseek-local-training-next.md`.

Completed receipts on Ubuntu under `/home/grant/decomp/local-research/runs/`:

| Run | Result |
|---|---|
| `870c0b0d99a0486a9df1602a61060128` | Installed gpt-oss:20b request hit its timeout; one call charged to the local call budget, no compiler work. |
| `16d138e8510e4ed98042e1525a90b348` | Installed Qwen coder responded; source lacked the required parameter placeholder and was rejected before compilation. |
| `0d9f92dcd65d488ebfa23e1dc98e9a85` | Two local coder calls, 12 compiler checks, 35 seconds. One C89 compile failure and one disproven instruction-count prediction; zero confirmed findings. The second request contained the first experiment's failure evidence. |
| `c1eb9dd20cfd4268a5fc0cbb1b258772` | Started through the live dashboard API. Stop interrupted the active model request. Terminal receipt: stopped, one call, zero compiles, seven seconds total. |

All model calls used installed local weights; no paid-provider requests were made.
The completed canary motivated an early-exit optimization: failed compilation or
falsification now skips remaining confirmation inputs. A positive finding still
requires both sources to compile and satisfy the prediction on all three inputs.

Final checks: **82 WSL Python tests passed**, **42 Windows tests passed with one
Linux-only skip**, and **two offline JavaScript UI tests passed**. WSL tests include
the existing synthetic corpus's real compiler checks and a process-descendant
cancellation regression. Existing POSIX compiler-recipe assertions fail on Windows
and pass under WSL; those unrelated tests were not changed.

The computer-use tool had no available browser, so no visual browser inspection is
claimed. Live HTTP control and offline DOM behavior were exercised. These receipts
verify the local research machinery, not model-weight improvement or game transfer.
