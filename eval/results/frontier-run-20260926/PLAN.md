# Deterministic frontier execution, September 26

User approved running the delivered deterministic queue and requested first-principles reasoning.

1. Establish fresh native checkpoint, attempts, exact membership and scheduler baseline.
2. Execute the existing frozen controller in successive 15-item deterministic-only batches until no eligible deterministic profile remains. Preserve existing per-profile budgets. The 1,000-batch ceiling catches an unexpected eligibility cycle; it is not a match target.
3. Keep each batch's full logs, source hashes, attempt lineage and ratchet receipt. Stop on a failed batch, model activity, lost exact, external pause, or STOP request. Preserve control-side pause throughout and restore native pause at every drained batch boundary.
4. In parallel, privately regenerate audioThreadMain from its retained incumbent and inspect one register-selection cause. Do not read reference or prior winning source as proposal input.
5. Deduplicate accepted gains against both baseline raw ledgers, separate recertification from new solving, and choose the next mechanism using measured compiler effects.

Execution uses reviewed delivery tooling without changing frozen code, production source, model budgets or integration settings. Deterministic-only intentionally skips integration/runtime capture. All compiles use native WSL build trees; CPU allocation is four, with three campaign workers and at most one additional private compile.

First-principles criterion: the target bytes and actual compiler behavior decide. Eligibility is an opportunity, score improvement is a partial observation, and only ordinary frontend plus object-certificate acceptance establishes an exact result.

Run review: the independent audit found that the reused canary checks matches and models but does not stop on a newly parked node. The per-batch runner now conservatively rejects any new parked status before launching another batch. Batch 1 had none; the already-running batch 2 is being checked manually. Subsequent child processes read the amended runner. This changes operational stopping only, not solver behavior or frozen pins.
