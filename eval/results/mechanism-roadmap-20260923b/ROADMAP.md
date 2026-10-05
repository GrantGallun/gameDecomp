# Mechanism roadmap

## Build (diff states the fix, source map locates it, nothing covers it)

| class | blocker weight | sole blocker | functions | uncovered | located |
|---|---:|---:|---:|---:|---:|
| `field:offset` | 10.75 | 2 | 125 | 58 | 1.0 |
| `field:immediate` | 8.38 | 1 | 112 | 55 | 1.0 |
| `extra:addiu` | 2.57 | 0 | 110 | 80 | 1.0 |
| `extra:sll` | 2.27 | 0 | 109 | 88 | 1.0 |
| `opcode:sh/sw` | 1.82 | 1 | 51 | 40 | 1.0 |
| `field:symbol` | 1.56 | 0 | 18 | 10 | 1.0 |
| `extra:andi` | 1.29 | 0 | 86 | 68 | 1.0 |
| `opcode:lh/lw` | 1.21 | 1 | 46 | 44 | 1.0 |
| `extra:sra` | 0.95 | 0 | 84 | 70 | 1.0 |
| `opcode:lw/sw` | 0.46 | 0 | 19 | 13 | 1.0 |
| `opcode:lh/lhu` | 0.21 | 0 | 19 | 14 | 1.0 |
| `extra:srl` | 0.17 | 0 | 7 | 5 | 1.0 |
| `opcode:lbu/lw` | 0.13 | 0 | 34 | 30 | 1.0 |
| `opcode:sb/sw` | 0.11 | 0 | 25 | 24 | 1.0 |
| `opcode:lhu/lw` | 0.09 | 0 | 42 | 35 | 1.0 |

## Needs a model (the diff does not state a source fix)

| class | blocker weight | sole blocker | functions |
|---|---:|---:|---:|
| `field:register` | 38.76 | 15 | 194 |
| `field:multi` | 19.09 | 2 | 178 |
| `field:branch` | 10.79 | 0 | 146 |
| `field:shape` | 7.2 | 2 | 80 |
| `missing:addiu` | 4.97 | 0 | 108 |
| `extra:sw` | 4.68 | 0 | 111 |
| `missing:lw` | 4.36 | 0 | 99 |
| `missing:sw` | 4.22 | 0 | 101 |
| `missing:move` | 3.72 | 0 | 73 |
| `extra:move` | 3.68 | 0 | 92 |

## Refine

| family | why | applications | broke | no-op | improve when acting | exact |
|---|---|---:|---:|---:|---:|---:|
| `commutative` | object unchanged | 853 | 0.007 | 0.836 | 0.179 | 0 |
| `decl_order` | object unchanged | 500 | 0.0 | 0.752 | 0.04 | 0 |
| `frontend_type` | breaks the build, object unchanged | 112 | 0.143 | 0.777 | 1.0 | 5 |
| `truth_test` | object unchanged | 31 | 0.0 | 0.871 | 0.5 | 0 |

## Proposed gates (cross-fitted; install only after a rerun loses no exact)

| family | skip when | applications | improving | functions |
|---|---|---:|---:|---:|
| `local_type` | recipe = O1 | 187 | 0 | 18 |
| `commutative` | axis = ordering | 88 | 0 | 6 |
