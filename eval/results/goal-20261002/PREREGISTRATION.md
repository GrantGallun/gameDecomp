# Sealed near-miss split — pre-registration (2026-10-02)

Written before the freeze; its sha256 is the split salt.

- Pool: functions in src TUs with a compiled attempt of score >= 80 in the KB or campaign ledger, never exact in either,
  not in `already_matched`, any eval set, heldout50, chain-vs-search, composed-edits A/B.
- Split: TU-disjoint, 50% of TUs sealed, by sha256(salt:tu).
- Looks: at most 3, each with a control arm (pinned start candidate) and a treatment arm at EQUAL compiler-call budget.
- Primary metric: TU-clustered two-sided sign test on (treatment - control) best score, alpha .05.
  Secondary: new exact functions, lost exacts (must be 0), stratified by tier. Exact counts alone never promote a generator.
- Promotion of a generator to default requires: primary p<.05, zero lost exacts, whole-ROM gate on every new exact.
- Development (tuning, mining, training, rule tables) may use dev names only. Corpus-wide data builders must exclude sealed names.
- Known weakness, stated in advance: sealed functions were seen by earlier tools. Not a clean-transfer claim.
