# Hourly campaign maintenance

Time: 2026-09-20T14:57:22.0941943-05:00
Run: resume-pipeline-20260908

- Cause/blocker: The initial WSL Ubuntu read of health.json and service.json failed before execution with Access is denied, Wsl/Service/E_ACCESSDENIED (exit 1). The underlying reason for the denial is unverified.
- Changes: This report only. No code, checkpoints, snapshot pins, candidates, receipts, model settings, correctness gates, or service state were changed. Any existing needs_repair state was left untouched.
- Checks: Read OPERATIONS.md first. Attempted one WSL status read; it failed. Checked service.pause existence through Windows filesystem metadata. No focused tests or live validation could run.
- Limitations: Health, service-control pause state, worker locks, frozen pins, and checkpoint contents remain unverified. No Windows fallback read of live JSON, amendment, permission workaround, or resume was attempted.
- Run status: Live run status unverified. service.pause was absent at the metadata check; service-control.json could not be checked through WSL.
- Next step: Repeat maintenance when authorized WSL access is available; diagnose any campaign failure before amending or resuming this same run.
