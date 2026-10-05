# Restarting from the best node vs one longer search (equal budget)

All 224 population functions, code-v9, same scheduler (PROTOCOL.md).

| | exact | only in this arm | compiles |
|---|---:|---|---:|
| long: one search of 96 | 17 | - | 17,301 |
| restart: 32 + 32 from best + 32 from best | 18 | updateRacePlayerMode40Stun | 17,749 |

Round gains: round 2 updateRacePlayerMode40Stun, round 3 MusAsk. Open functions: restart better 26, long better 27.
Verdict by the pre-registered rule: restart adopted (18 > 17, nothing long-only) -- but by one function, and a wash
on the open functions. Restarting is at least as good as the same budget spent in one search, not dramatically
better. No new inventory: every exact of both arms was already recorded (377 / SOLVED 274).
