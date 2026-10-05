# Goal: trustworthy generator measurement, then new generators (started 2026-10-02)

Loop per round: build -> test -> Opus audit (adversarial, read-only) -> fix findings -> record. Do not stop while an item is open.

- [x] R1 build eval/seal.py + tests; Opus audit; fixes (9 tests)
- [x] R1b prereg + frozen manifest eval/sets/sbk1_v5_sealed_nearmiss.json (121 sealed/29 TUs, 95 dev; audit clean)
- [x] R2 DONE after Opus audit: sealed_in_sets fails loud (pinned digest, env override), TU-level exclusion in rule_mine, dev no longer blocked in trajectory_factory/repair_dataset, export_fresh_repairs + tool_action filtered, 20 seal tests; addendum.json/ADDENDUM.md recorded pre-look. OPEN (frozen-tree amendment, not done): transition_policy, layoutstore, sibling pools, campaign resume; mined_rules.json rebuild enforce: campaign scheduler + rule_mine + repair_dataset + trajectory_factory exclude sealed names; tests per consumer; Opus audit
- [x] R3 DONE after Opus audit: found that the dev 'null' was a no-op (treatment compiled identical sources in all 91 fns; narrow_update.variants fires on 0/2108 dev sources). Fixed: divergence recorded per fn + sealed look REFUSED unless dev divergence >= 5 (--dev-report); best-scoring attempt per arm; per-arm crash handling; errors/ties/spend/excluded-row test + treatment spec hashed INSIDE the ledger entry; 24 seal tests. OPEN: trial-DB hash, start-hash check in look(), frontend-vs-raw exact, header drift between arms. eval/seal_run.py built + 3 tests + real-IDO dev smoke (3 fns, 50s). Opus audit of R3 still TODO. seal runner: one command runs control vs treatment at equal budget on sealed set, calls look(); Opus audit
- [~] R4 REDIRECTED (v1 probe had a header-line bug, found by Opus; v2 corrected: stall-length hazard knee in pooled data (8%->1.4%) is heterogeneity — within the 300 functions that stall >=100, hazard is 0.3% low-k vs 1.4% high-k, so NO stall-based stop/restart trigger either). Drift/plateau detector premise tested and FALSIFIED on 398 non-sealed fns/85k edits (probe_info_rate*.py): exact-null edits 0.2%; same-shape rate 77% eventually-exact vs 74% never-exact -> cannot separate plateau from solvable; detector NOT built. Remaining: aligned hunk view (detector-free) + fixed-schedule restart vs single long search at equal budget on dev. (old: edit-mining loop (source change -> diff change over matched corpus + dev near-misses); catalog w/ provenance; Opus audit
- [~] R5 narrow_updates DEV RESULT: 95 fns, 0 TUs up/down, 0 new exact, 4 errors (sqlite lock, timeout added). NO sealed look spent. v1 dev run re-done as seal-run-dev-narrow-v2 with the fixed runner. (old: dev run of narrow_updates relaunched (first launch died with its WSL session) -> /home/grant/decomp/experiments/seal-run-dev-narrow; then sealed look ONCE. first measured look on narrow_update (dev first, then sealed once); record honestly

## State after Opus brainstorm on narrow_update (2026-10-02)
- Census (eval/applicability_census.py, 95 dev near-misses, no compiles): narrow_update 0, scoped_field 0, counted_loop 0,
  branch_shape 0 (valid: fires through the same path on its motivating fixture once recorded evidence is passed; first
  census run was a silent decline from missing evidence), unaligned_copy 0, temp_copyback 1, scalar_coalesce 4, regalloc_mutations 94/95.
  Narrow generators are catalog-only; none can be judged by the sealed instrument (need applicability >= ~5-10%).
- Opus: sealed instrument can only judge broad mechanisms. Next dev treatment = site_edits in front of regalloc search at equal
  per-function cap (seal_run --treatment site_edits_first). Smoke 4 fns: 4/4 diverged. Full dev run -> /home/grant/decomp/experiments/seal-run-dev-site.
- Sealed look still NOT spent; gated on dev divergence >= 5 AND a pre-registered addendum for the applicable-subset rule (TODO).
- Mined-rules rebuild under the seal is a prerequisite for any mined-lane treatment.

## PAUSED 2026-10-02 (user request)
- CORRECTION: the paused dev run (SIGSTOPped) was killed when its background wrapper hit the 50-minute limit; it CANNOT be resumed. 7/95 done, partial dir seal-run-dev-site is debris; relaunch into a new --out. (Original note: the run was SIGSTOPped, resume via kill -CONT, now invalid.) 7 of 95 functions done, 315 attempts in
  /home/grant/decomp/experiments/seal-run-dev-site/trial.sqlite (committed per attempt). log.json/report.json only
  appear at the end; a Windows restart or `wsl --shutdown` kills the process and the run would have to be redone
  into a new --out directory (the old one cannot be reused).
- llama-server (Windows pid 30184) stopped. Ollama (pid 24520) and the unattended campaign (campaign_service 547,
  fast_campaign 1056152) were NOT touched.
- Sealed look still unspent. Next: resume/finish dev run, check summary.diverged >= 5, then decide on the sealed look.
