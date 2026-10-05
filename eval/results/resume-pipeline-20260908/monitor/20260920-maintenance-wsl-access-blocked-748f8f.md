# Hourly maintenance — 2026-09-20

- Read `OPERATIONS.md` before attempting live-state inspection.
- Blocker: the WSL command to read `health.json`, `service.json`, `service-control.json`, and check `service.pause` exited with code 1: `Access is denied. Error code: Wsl/Service/E_ACCESSDENIED`.
- Run status: unverified. Pause state, worker/lock state, frozen pins, and checkpoint contents could not be inspected through WSL. No Windows fallback readers were used for live state.
- Changes: this report only. No campaign code, checkpoint, settings, candidates, receipts, correctness gates, or service controls were changed; any existing `needs_repair` state remains untouched.
- Validation: the initial WSL state-read attempt failed before producing campaign data. Focused tests and runtime validation could not be performed. No amendment or resume was attempted.
- Remaining limitation: maintenance requires an authorized session with working WSL access. No identical retry, permission bypass, installation, or WSL shutdown was attempted.
