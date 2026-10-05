# Hourly campaign maintenance

Time: 2026-09-20T04:22:30.1443513Z
Run: eval/results/resume-pipeline-20260908

Cause/blocker: The required WSL live-state read failed before Python started: Access is denied; Wsl/Service/E_ACCESSDENIED (exit 1). OPERATIONS.md was read first. health.json and service.json could not be read; the underlying campaign condition is unverified.

Pause check: service.pause was absent at the bounded Windows metadata check; service-control.json could not be read through WSL, so pause state is otherwise unverified.

Changes: This report only. No code, checkpoints, accepted candidates, receipts, frozen pins, model settings, correctness gates, test inputs, budgets, or service controls were changed. Any existing needs_repair state was left untouched. No snapshot amendment or resume was attempted.

Validation: One WSL read attempt failed at the WSL service boundary. No focused tests ran because no fix was made and required live validation was unavailable. The identical failing command was not retried; no escalation or permission bypass was attempted.

Run status and limitations: Live health, completion, service-control pause state, worker lock ownership, and frozen-pin validity remain unverified. Safe maintenance requires permitted WSL access; perform the normal live-state and pin checks before any amendment or resume.