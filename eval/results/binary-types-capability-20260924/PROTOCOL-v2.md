# Protocol: capability run 2 (generator v2)

Written 2026-09-24 before `capability_v2.py` ran. Same set rule (every workspace function without an exact attempt
in the KB, now 1,455), harness, confirmation, contamination screen and recording as `PROTOCOL.md`.
Changes, each motivated by run 1's compile-failure buckets on this production set (not by any reference label):
- identity v4 (struct-identity A4): table elements reached as `table + index` are observed; a symbol with element
  accesses and a stride found in m2c's pass-one output is declared as an array of an element struct;
- stride detection also reads `(i * N) + sym` and `sym + (i * N)`;
- the function's own prototype stays in m2c's context but not in the compile header (run 1: 69 functions refused as
  "incompatible function return type" where m2c defined a different return type than the binary guess).
The frozen CHECK measurement (`context-ablation-20260924`) is not re-run: v2 numbers are production yield, not a
held-out rate. Strategy for recorded matches: `binary-types-v2-20260924:source-independent`.
