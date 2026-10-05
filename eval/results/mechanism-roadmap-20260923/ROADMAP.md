# Mechanism roadmap

## Build (diff states the fix, source map locates it, nothing covers it)

| class | blocker weight | sole blocker | functions | uncovered | located |
|---|---:|---:|---:|---:|---:|
| `field:offset` | 10.75 | 2 | 129 | 63 | 1.0 |
| `field:immediate` | 8.09 | 1 | 118 | 62 | 1.0 |
| `extra:addiu` | 2.54 | 0 | 116 | 89 | 1.0 |
| `extra:sll` | 2.26 | 0 | 117 | 96 | 1.0 |
| `opcode:sh/sw` | 1.82 | 1 | 48 | 37 | 1.0 |
| `field:symbol` | 1.56 | 0 | 17 | 10 | 1.0 |
| `extra:andi` | 1.29 | 0 | 91 | 73 | 1.0 |
| `opcode:lh/lw` | 1.21 | 1 | 44 | 42 | 1.0 |
| `extra:sra` | 0.94 | 0 | 90 | 76 | 1.0 |
| `opcode:lw/sw` | 0.46 | 0 | 17 | 11 | 1.0 |
| `opcode:lh/lhu` | 0.21 | 0 | 19 | 13 | 1.0 |
| `extra:srl` | 0.17 | 0 | 13 | 11 | 1.0 |
| `opcode:lbu/lw` | 0.13 | 0 | 32 | 28 | 1.0 |
| `opcode:sb/sw` | 0.11 | 0 | 24 | 23 | 1.0 |
| `opcode:lhu/lw` | 0.09 | 0 | 39 | 32 | 1.0 |

## Needs a model (the diff does not state a source fix)

| class | blocker weight | sole blocker | functions |
|---|---:|---:|---:|
| `field:register` | 42.06 | 18 | 197 |
| `field:multi` | 18.55 | 2 | 185 |
| `field:branch` | 11.01 | 0 | 153 |
| `field:shape` | 7.33 | 2 | 78 |
| `missing:addiu` | 4.97 | 0 | 106 |
| `extra:sw` | 4.9 | 0 | 116 |
| `missing:sw` | 4.22 | 0 | 107 |
| `missing:lw` | 4.11 | 0 | 94 |
| `missing:move` | 3.93 | 0 | 76 |
| `extra:move` | 3.9 | 0 | 98 |

## Refine

| family | why | applications | broke | no-op | improve when acting | exact |
|---|---|---:|---:|---:|---:|---:|
| `commutative` | object unchanged | 1157 | 0.005 | 0.872 | 0.176 | 0 |
| `decl_order` | object unchanged | 451 | 0.0 | 0.758 | 0.046 | 0 |
| `frontend_type` | breaks the build, object unchanged | 292 | 0.216 | 0.753 | 0.667 | 3 |
| `truth_test` | object unchanged | 31 | 0.0 | 0.871 | 0.5 | 0 |

## Proposed gates (cross-fitted; install only after a rerun loses no exact)

| family | skip when | applications | improving | functions |
|---|---|---:|---:|---:|
| `commutative` | state = frontend_rejected | 323 | 1 | 64 |
| `frontend_type` | axis = regalloc | 179 | 1 | 43 |
| `local_type` | axis = ordering | 74 | 0 | 7 |
