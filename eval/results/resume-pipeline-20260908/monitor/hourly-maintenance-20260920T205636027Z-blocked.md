# Hourly campaign maintenance — blocked

Run: `resume-pipeline-20260908`

- Cause: WSL Ubuntu access failed with `Wsl/Service/E_ACCESSDENIED` (exit code 1) before the requested live-state reads could execute. `OPERATIONS.md` was read first.
- Changes: This report only. No code, checkpoints, pins, candidates, receipts, model settings, correctness gates, or service controls were changed.
- Tests: Not run; WSL access prevented live validation. No repeated failing invocation or permission bypass was attempted.
- Limitations: `health.json`, `service.json`, pause controls, worker lock, and frozen pins could not be verified. No stopped-run amendment was attempted.
- Run status: Unverified and unchanged. Any existing `needs_repair` state remains untouched. No resume was attempted. Maintenance requires permitted WSL access before it can proceed.