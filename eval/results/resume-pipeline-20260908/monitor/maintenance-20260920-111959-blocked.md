# Hourly maintenance: blocked

Time: 2026-09-20 11:19:59 -05:00
Run: `eval/results/resume-pipeline-20260908`

- Read `OPERATIONS.md` first. The required WSL read of `health.json`, then `service.json` and pause controls failed before reading any files: `wsl.exe -d Ubuntu -- bash -lc ...` exited 1 with `Wsl/Service/E_ACCESSDENIED` (Access is denied).
- A Windows existence-only check found no `service.pause` marker. `service-control.json` was not read; pause state and current run health remain unverified. No Windows live checkpoint reads were attempted.
- Changes: this report only. No campaign code, checkpoint, snapshot, pins, candidates, receipts, model settings, correctness gates, or service controls were changed. Existing `needs_repair`, if present, was left untouched.
- Validation: WSL access failed once and was not retried. Focused tests, frozen-pin verification, and worker-lock verification could not be performed through WSL. No campaign failure diagnosis or semantic result is claimed.
- Run status: not resumed. No runtime amendment deployed. Permission to access WSL from the authorized execution environment is the concrete blocker to validation and resumption; no escalation or bypass was attempted.
