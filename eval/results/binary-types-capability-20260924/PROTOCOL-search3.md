# Protocol: search round 3 (deeper, restarted) on every compiled-not-exact binary-typed draft

Written 2026-09-24 before the run. Same stream and recording as `PROTOCOL-search.md`; only the budget changes:
32 compiles, then two restarts of 32 from the best node (96 in all), on every function whose latest draft compiled and
is not exact in the KB (rounds 1-2 left 387 improved and 412 flat). Driven by `search.py` with
SEARCH_BUDGET=32 SEARCH_RESTART=32 SEARCH_RESTARTS=2 SEARCH_OUT=search3. Strategy for recorded matches:
`binary-types-search3-20260924:source-independent`.
