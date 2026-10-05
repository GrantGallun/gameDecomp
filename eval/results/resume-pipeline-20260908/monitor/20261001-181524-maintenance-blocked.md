Hourly campaign maintenance: blocked

Run: resume-pipeline-20260908

Cause: The initial WSL live-state read failed with exit code 1: Access is denied, Wsl/Service/E_ACCESSDENIED. The underlying permission cause is unverified. OPERATIONS.md was read first; health.json and service.json could not be read through WSL. No repeated attempt or permission escalation was made.

Pause check: service.pause is absent; service-control.json reports paused=false. The control file was read from Windows; no live checkpoint was opened with a Windows reader.

Changes: Only this maintenance report was added. No campaign code, checkpoint, accepted candidate, receipt, frozen pin, model setting, correctness gate, or service state was changed. No runtime amendment was deployed and needs_repair was not cleared.

Validation: WSL access check failed before campaign validation or focused tests could run. Frozen pins and campaign.lock ownership could not be verified. No semantic pass or test success is claimed.

Run status: Unknown from live state; maintenance is blocked by WSL access. No resume was attempted. Existing dirty changes and campaign artifacts were left intact. Validation and any resumption require permitted WSL access.
