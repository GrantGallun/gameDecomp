# Current residual patterns: checkpoint 3707

One selected compiled nonexact candidate per checkpoint function. Textual residual patterns are hypotheses; no source/callee behavior inferred. Byte totals overlap across patterns. Baseline opcode dialect may differ from object diffs.

Analysed 1153 functions; 27 selected records unavailable.

## target opcodes

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `addiu` | 888 | 6467 | 426956 |
| `lw` | 802 | 7683 | 393932 |
| `sw` | 774 | 8780 | 393308 |
| `lui` | 697 | 4392 | 363316 |
| `li` | 566 | 2433 | 314100 |
| `addu` | 564 | 3319 | 318120 |
| `bnez` | 510 | 1532 | 296324 |
| `sh` | 507 | 1923 | 235744 |
| `beqz` | 499 | 1690 | 319240 |
| `andi` | 499 | 1322 | 273648 |
| `move` | 493 | 2182 | 303224 |
| `bne` | 492 | 1298 | 260864 |
| `lh` | 458 | 2431 | 289788 |
| `sll` | 434 | 2568 | 287348 |
| `lhu` | 376 | 983 | 201392 |
| `b` | 375 | 1377 | 251236 |
| `nop` | 346 | 1356 | 265428 |
| `lbu` | 322 | 1472 | 198636 |
| `sra` | 256 | 1747 | 194020 |
| `beq` | 246 | 522 | 161248 |

## candidate opcodes

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `addiu` | 869 | 6142 | 420800 |
| `lw` | 810 | 9753 | 398880 |
| `sw` | 790 | 10359 | 397572 |
| `lui` | 689 | 4199 | 364588 |
| `li` | 618 | 7122 | 341032 |
| `addu` | 544 | 3013 | 309284 |
| `sh` | 507 | 1884 | 234328 |
| `beqz` | 504 | 1737 | 317520 |
| `move` | 496 | 2498 | 314924 |
| `bne` | 495 | 1341 | 259748 |
| `andi` | 483 | 1323 | 277440 |
| `lh` | 458 | 2527 | 293584 |
| `bnez` | 456 | 1446 | 267900 |
| `sll` | 432 | 2589 | 280640 |
| `b` | 368 | 1382 | 247084 |
| `lhu` | 366 | 927 | 191480 |
| `lbu` | 311 | 1590 | 194428 |
| `nop` | 303 | 1201 | 228580 |
| `sra` | 286 | 1945 | 219844 |
| `beq` | 248 | 545 | 163644 |

## single instruction families

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `register_operands` | 774 | 2523 | 369424 |
| `control_operands` | 464 | 1759 | 285716 |
| `stack_frame_adjustment` | 326 | 540 | 223148 |
| `opcode_substitution` | 260 | 366 | 152552 |
| `stack_slot_offset` | 179 | 363 | 108208 |
| `other_same_opcode_operands` | 179 | 270 | 96248 |
| `relocation_expression` | 66 | 108 | 43160 |
| `load_signedness_opcode` | 10 | 10 | 6804 |
| `memory_offset` | 5 | 7 | 1488 |

## replacement signatures

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `lw R0,0x18(R1) => lw R2,0x18(R1)` | 72 | 74 | 16772 |
| `lhu R0,0x2a(R1) => lhu R2,0x2a(R1)` | 64 | 68 | 9220 |
| `addu R0,R1,R2 => addu R0,R0,R2` | 49 | 49 | 7396 |
| `sw R0,0x18(R1) => sw R2,0x18(R1)` | 37 | 37 | 5884 |
| `move R0,R1 => move R0,R2` | 36 | 69 | 23328 |
| `sh R0,%lo(gEndingCreditsSequencePhase)(R1) => sh R2,%lo(gEndingCreditsSequencePhase)(R1)` | 34 | 34 | 5376 |
| `li R0,1 => li R1,1` | 27 | 33 | 15724 |
| `sw R0,0x7c(R1) ; lw R2,0x2fc(R1) => sw R3,0x7c(R1) ; lw R4,0x2fc(R1)` | 25 | 25 | 10704 |
| `lw R0,0x18(sp) => lw R0,0x1c(sp)` | 23 | 23 | 3200 |
| `addiu sp,sp,0x40 => addiu sp,sp,0x48` | 22 | 22 | 14040 |
| `sw R0,0(R1) => sw R2,0(R3)` | 21 | 33 | 12292 |
| `lh R0,0x18(R1) => lh R2,0x18(R1)` | 21 | 25 | 15112 |
| `addu R0,R0,R1 => addu R0,R0,R2` | 21 | 22 | 26796 |
| `sw R0,0x18(sp) => sw R0,0x1c(sp)` | 21 | 21 | 2708 |
| `lhu R0,0x1c(R1) => lhu R2,0x1c(R1)` | 20 | 36 | 10568 |
| `lw R0,0x2fc(R1) => lw R2,0x2fc(R1)` | 20 | 27 | 19424 |
| `sw R0,0(R1) => sw R0,0(R2)` | 20 | 27 | 13524 |
| `lw R0,0(R1) => lw R2,0(R1)` | 20 | 22 | 14328 |
| `sw R0,0x1c(sp) => sw R1,0x1c(sp)` | 17 | 19 | 13432 |
| `jr R0 => jr R1` | 17 | 17 | 20504 |

## target ngrams

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `sw sw` | 368 | 2283 | 250820 |
| `sw lw` | 338 | 2345 | 231004 |
| `lui addiu` | 317 | 1475 | 215048 |
| `lw lw` | 290 | 1631 | 193344 |
| `addiu sw` | 271 | 1498 | 179148 |
| `addiu addiu` | 234 | 571 | 157648 |
| `sw sw sw` | 219 | 656 | 172448 |
| `addiu andi` | 216 | 318 | 97200 |
| `sll addu` | 200 | 803 | 157564 |
| `lw lui` | 198 | 1248 | 154476 |
| `lw addiu` | 187 | 321 | 128992 |
| `sw lui` | 179 | 373 | 142540 |
| `addu lw` | 174 | 388 | 134044 |
| `addiu lw` | 169 | 233 | 129392 |
| `addiu sh` | 165 | 316 | 102424 |
| `addu sw` | 154 | 325 | 119852 |
| `lw lw lw` | 148 | 556 | 117200 |
| `lw nop` | 147 | 516 | 134716 |
| `slti bnez` | 138 | 241 | 117904 |
| `andi beqz` | 136 | 267 | 109504 |

## textually displaced

| Pattern | Functions | Occurrences | Target bytes represented |
|---|---:|---:|---:|
| `sw` | 249 | 1080 | 189128 |
| `nop` | 211 | 816 | 194492 |
| `addiu` | 184 | 335 | 131668 |
| `li` | 169 | 415 | 151308 |
| `jal` | 167 | 619 | 128896 |
| `lui` | 159 | 359 | 136200 |
| `lw` | 132 | 336 | 115476 |
| `move` | 120 | 198 | 111580 |
| `lh` | 94 | 138 | 92636 |
| `sll` | 82 | 191 | 87740 |
| `sra` | 63 | 158 | 60640 |
| `addu` | 54 | 121 | 66932 |
| `sh` | 53 | 105 | 46484 |
| `ori` | 48 | 116 | 43744 |
| `sb` | 48 | 91 | 38824 |
| `lbu` | 41 | 56 | 45204 |
| `mflo` | 38 | 116 | 43780 |
| `lhu` | 38 | 46 | 28636 |
| `b` | 37 | 39 | 20736 |
| `subu` | 22 | 36 | 28908 |
