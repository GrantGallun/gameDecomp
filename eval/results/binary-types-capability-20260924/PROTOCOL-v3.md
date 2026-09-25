# Protocol: capability run 3 (generator v3), on v2's not-compiled drafts

Written 2026-09-24 before `capability_v3.py` ran. Same harness, confirmation, contamination screen and recording as
`PROTOCOL.md`. Change: m2c runs with `--valid-syntax` and its output is lowered by the pipeline's existing
`solver.m2c_byte_view.lower` (as `solver.compile_recovery` does), aimed at run 2's 104 "Syntax Error" failures
(`? sp18`, `->unk-44C`). Applied only to functions whose v2 draft did not compile. First on 40 (a yield check), then
all if it converts any. Strategy for recorded matches: `binary-types-v3-20260924:source-independent`.
