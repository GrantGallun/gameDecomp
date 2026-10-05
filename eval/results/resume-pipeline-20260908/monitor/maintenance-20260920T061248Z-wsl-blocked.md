# Hourly campaign maintenance — 2026-09-20 06:12 UTC

- Run: `resume-pipeline-20260908`.
- Recorded status: both `health.json` and `service.json` say `needs_repair`; service records three consecutive failures, exit code 1, and no worker PID. Live process and lock ownership could not be verified.
- Pause check: `service.pause` is absent and `service-control.json` has `paused: false`.
- Latest recorded failure: `campaign_data.prepare()` in the frozen `code/eval/campaign_data.py`, line 62, raises `FileNotFoundError` for `/home/grant/decomp/runs/state/binary-data/600aa62682938376099abd395077e160e0555e4e520d9cdc32e16169175967b2.json`. Earlier recorded failures mention an unexpected campaign object store. The underlying cause remains unverified.
- Maintenance blocker: the first WSL command failed before executing Bash with `Wsl/Service/E_ACCESSDENIED`. No identical retry, escalation, or alternate WSL access was attempted.
- Checks: read `OPERATIONS.md` first; then read health, service, and control JSON using short Windows streams with `FileShare.ReadWrite | FileShare.Delete`, allowing atomic replacement. No live checkpoint was read through Windows.
- Changes: this report only. No code, checkpoint, frozen pins, accepted candidates, receipts, model settings, test inputs, budgets, or correctness gates were changed. No snapshot amendment was deployed.
- Tests and limitations: focused tests, live checkpoint validation, frozen-pin verification, and campaign lock inspection could not be performed through WSL. No test pass or semantic pass is claimed.
- Disposition: left `needs_repair` in place and did not resume. Further diagnosis and any repair/resumption require permitted WSL access and the prescribed validation and amendment safeguards.
