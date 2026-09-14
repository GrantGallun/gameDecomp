.set noat
.set noreorder
.set gp=64# This file is used by modern gas.
# Defines the expected assembly macros

# Evaluate this file only once in case it's included more than once
.ifndef _MACRO_INC_GUARD
.internal _MACRO_INC_GUARD
.set _MACRO_INC_GUARD, 1

# A function symbol.
.macro glabel label, visibility=global
    .\visibility \label
    .type \label, @function
    \label:
        .ent \label
.endm

# The end of a function symbol.
.macro endlabel label
    .size \label, . - \label
    .end \label
.endm

# An alternative entry to a function.
.macro alabel label, visibility=global
    .\visibility \label
    .type \label, @function
    \label:
        .aent \label
.endm

# A label referenced by an error handler table.
.macro ehlabel label, visibility=global
    .\visibility \label
    \label:
.endm


# A label referenced by a jumptable.
.macro jlabel label, visibility=global
    .\visibility \label
    \label:
.endm


# A data symbol.
.macro dlabel label, visibility=global
    .\visibility \label
    .type \label, @object
    \label:
.endm

# End of a data symbol.
.macro enddlabel label
    .size \label, . - \label
.endm


# Label to signal the symbol haven't been matched yet.
.macro nonmatching label, size=1
    .global \label\().NON_MATCHING
    .type \label\().NON_MATCHING, @object
    .size \label\().NON_MATCHING, \size
    \label\().NON_MATCHING:
.endm

# COP0 register aliases

.set Index,         $0
.set Random,        $1
.set EntryLo0,      $2
.set EntryLo1,      $3
.set Context,       $4
.set PageMask,      $5
.set Wired,         $6
.set Reserved07,    $7
.set BadVaddr,      $8
.set Count,         $9
.set EntryHi,       $10
.set Compare,       $11
.set Status,        $12
.set Cause,         $13
.set EPC,           $14
.set PRevID,        $15
.set Config,        $16
.set LLAddr,        $17
.set WatchLo,       $18
.set WatchHi,       $19
.set XContext,      $20
.set Reserved21,    $21
.set Reserved22,    $22
.set Reserved23,    $23
.set Reserved24,    $24
.set Reserved25,    $25
.set PErr,          $26
.set CacheErr,      $27
.set TagLo,         $28
.set TagHi,         $29
.set ErrorEPC,      $30
.set Reserved31,    $31

# Float register aliases

.set $fv0,          $f0
.set $fv0f,         $f1
.set $fv1,          $f2
.set $fv1f,         $f3
.set $ft0,          $f4
.set $ft0f,         $f5
.set $ft1,          $f6
.set $ft1f,         $f7
.set $ft2,          $f8
.set $ft2f,         $f9
.set $ft3,          $f10
.set $ft3f,         $f11
.set $fa0,          $f12
.set $fa0f,         $f13
.set $fa1,          $f14
.set $fa1f,         $f15
.set $ft4,          $f16
.set $ft4f,         $f17
.set $ft5,          $f18
.set $ft5f,         $f19
.set $fs0,          $f20
.set $fs0f,         $f21
.set $fs1,          $f22
.set $fs1f,         $f23
.set $fs2,          $f24
.set $fs2f,         $f25
.set $fs3,          $f26
.set $fs3f,         $f27
.set $fs4,          $f28
.set $fs4f,         $f29
.set $fs5,          $f30
.set $fs5f,         $f31

.endif
nonmatching drawMenuSpriteWithAlphaClipped, 0x704

glabel drawMenuSpriteWithAlphaClipped
    /* 10570 8000F970 27BDFF58 */  addiu      $sp, $sp, -0xA8
    /* 10574 8000F974 AFA400A8 */  sw         $a0, 0xA8($sp)
    /* 10578 8000F978 AFA500AC */  sw         $a1, 0xAC($sp)
    /* 1057C 8000F97C AFA700B4 */  sw         $a3, 0xB4($sp)
    /* 10580 8000F980 8CCE0004 */  lw         $t6, 0x4($a2)
    /* 10584 8000F984 97A800BA */  lhu        $t0, 0xBA($sp)
    /* 10588 8000F988 000E78C0 */  sll        $t7, $t6, 3
    /* 1058C 8000F98C 01E6C021 */  addu       $t8, $t7, $a2
    /* 10590 8000F990 27190008 */  addiu      $t9, $t8, 0x8
    /* 10594 8000F994 29010201 */  slti       $at, $t0, 0x201
    /* 10598 8000F998 102001B4 */  beqz       $at, .L8001006C
    /* 1059C 8000F99C AFB9009C */   sw        $t9, 0x9C($sp)
    /* 105A0 8000F9A0 190001B2 */  blez       $t0, .L8001006C
    /* 105A4 8000F9A4 00000000 */   nop
    /* 105A8 8000F9A8 97A700BE */  lhu        $a3, 0xBE($sp)
    /* 105AC 8000F9AC 00000000 */  nop
    /* 105B0 8000F9B0 28E10201 */  slti       $at, $a3, 0x201
    /* 105B4 8000F9B4 102001AD */  beqz       $at, .L8001006C
    /* 105B8 8000F9B8 00000000 */   nop
    /* 105BC 8000F9BC 18E001AB */  blez       $a3, .L8001006C
    /* 105C0 8000F9C0 AFA600B0 */   sw        $a2, 0xB0($sp)
    /* 105C4 8000F9C4 93AE00C3 */  lbu        $t6, 0xC3($sp)
    /* 105C8 8000F9C8 3C048015 */  lui        $a0, %hi(gMenuViewportCenterX)
    /* 105CC 8000F9CC 31D80003 */  andi       $t8, $t6, 0x3
    /* 105D0 8000F9D0 3C0E800B */  lui        $t6, %hi(gMenuSpriteFlipScales)
    /* 105D4 8000F9D4 25CE51F0 */  addiu      $t6, $t6, %lo(gMenuSpriteFlipScales)
    /* 105D8 8000F9D8 0018C880 */  sll        $t9, $t8, 2
    /* 105DC 8000F9DC 032E1021 */  addu       $v0, $t9, $t6
    /* 105E0 8000F9E0 844F0000 */  lh         $t7, 0x0($v0)
    /* 105E4 8000F9E4 97AE00B6 */  lhu        $t6, 0xB6($sp)
    /* 105E8 8000F9E8 84580002 */  lh         $t8, 0x2($v0)
    /* 105EC 8000F9EC A7AF0076 */  sh         $t7, 0x76($sp)
    /* 105F0 8000F9F0 000E78C0 */  sll        $t7, $t6, 3
    /* 105F4 8000F9F4 00CF5021 */  addu       $t2, $a2, $t7
    /* 105F8 8000F9F8 A7B80074 */  sh         $t8, 0x74($sp)
    /* 105FC 8000F9FC 9146000E */  lbu        $a2, 0xE($t2)
    /* 10600 8000FA00 87B800AA */  lh         $t8, 0xAA($sp)
    /* 10604 8000FA04 01060019 */  multu      $t0, $a2
    /* 10608 8000FA08 8484660E */  lh         $a0, %lo(gMenuViewportCenterX)($a0)
    /* 1060C 8000FA0C 3C058015 */  lui        $a1, %hi(gMenuViewportCenterY)
    /* 10610 8000FA10 84A56610 */  lh         $a1, %lo(gMenuViewportCenterY)($a1)
    /* 10614 8000FA14 87AF00AE */  lh         $t7, 0xAE($sp)
    /* 10618 8000FA18 03047021 */  addu       $t6, $t8, $a0
    /* 1061C 8000FA1C 01E5C021 */  addu       $t8, $t7, $a1
    /* 10620 8000FA20 9149000F */  lbu        $t1, 0xF($t2)
    /* 10624 8000FA24 000EC880 */  sll        $t9, $t6, 2
    /* 10628 8000FA28 00187080 */  sll        $t6, $t8, 2
    /* 1062C 8000FA2C AFB90098 */  sw         $t9, 0x98($sp)
    /* 10630 8000FA30 240BFFFF */  addiu      $t3, $zero, -0x1
    /* 10634 8000FA34 00007812 */  mflo       $t7
    /* 10638 8000FA38 000FC080 */  sll        $t8, $t7, 2
    /* 1063C 8000FA3C 00187943 */  sra        $t7, $t8, 5
    /* 10640 8000FA40 00E90019 */  multu      $a3, $t1
    /* 10644 8000FA44 01F9C021 */  addu       $t8, $t7, $t9
    /* 10648 8000FA48 AFB80090 */  sw         $t8, 0x90($sp)
    /* 1064C 8000FA4C 254A0008 */  addiu      $t2, $t2, 0x8
    /* 10650 8000FA50 AFAE0094 */  sw         $t6, 0x94($sp)
    /* 10654 8000FA54 00006025 */  or         $t4, $zero, $zero
    /* 10658 8000FA58 00006825 */  or         $t5, $zero, $zero
    /* 1065C 8000FA5C 00007812 */  mflo       $t7
    /* 10660 8000FA60 000FC880 */  sll        $t9, $t7, 2
    /* 10664 8000FA64 0019C143 */  sra        $t8, $t9, 5
    /* 10668 8000FA68 87B90076 */  lh         $t9, 0x76($sp)
    /* 1066C 8000FA6C 030E7821 */  addu       $t7, $t8, $t6
    /* 10670 8000FA70 172B0004 */  bne        $t9, $t3, .L8000FA84
    /* 10674 8000FA74 AFAF008C */   sw        $t7, 0x8C($sp)
    /* 10678 8000FA78 24CCFFFF */  addiu      $t4, $a2, -0x1
    /* 1067C 8000FA7C 000CC140 */  sll        $t8, $t4, 5
    /* 10680 8000FA80 03006025 */  or         $t4, $t8, $zero
  .L8000FA84:
    /* 10684 8000FA84 87AE0074 */  lh         $t6, 0x74($sp)
    /* 10688 8000FA88 AFA70000 */  sw         $a3, 0x0($sp)
    /* 1068C 8000FA8C 15CB0006 */  bne        $t6, $t3, .L8000FAA8
    /* 10690 8000FA90 AFA80004 */   sw        $t0, 0x4($sp)
    /* 10694 8000FA94 252DFFFF */  addiu      $t5, $t1, -0x1
    /* 10698 8000FA98 000D7940 */  sll        $t7, $t5, 5
    /* 1069C 8000FA9C 01E06825 */  or         $t5, $t7, $zero
    /* 106A0 8000FAA0 AFA70000 */  sw         $a3, 0x0($sp)
    /* 106A4 8000FAA4 AFA80004 */  sw         $t0, 0x4($sp)
  .L8000FAA8:
    /* 106A8 8000FAA8 87B900D2 */  lh         $t9, 0xD2($sp)
    /* 106AC 8000FAAC 00000000 */  nop
    /* 106B0 8000FAB0 00B9C023 */  subu       $t8, $a1, $t9
    /* 106B4 8000FAB4 87B900DA */  lh         $t9, 0xDA($sp)
    /* 106B8 8000FAB8 00187480 */  sll        $t6, $t8, 18
    /* 106BC 8000FABC 00B9C021 */  addu       $t8, $a1, $t9
    /* 106C0 8000FAC0 87B900CE */  lh         $t9, 0xCE($sp)
    /* 106C4 8000FAC4 000E1C03 */  sra        $v1, $t6, 16
    /* 106C8 8000FAC8 00187480 */  sll        $t6, $t8, 18
    /* 106CC 8000FACC 0099C023 */  subu       $t8, $a0, $t9
    /* 106D0 8000FAD0 87B900D6 */  lh         $t9, 0xD6($sp)
    /* 106D4 8000FAD4 000E3C03 */  sra        $a3, $t6, 16
    /* 106D8 8000FAD8 00187480 */  sll        $t6, $t8, 18
    /* 106DC 8000FADC 0099C021 */  addu       $t8, $a0, $t9
    /* 106E0 8000FAE0 000E1403 */  sra        $v0, $t6, 16
    /* 106E4 8000FAE4 8FB90098 */  lw         $t9, 0x98($sp)
    /* 106E8 8000FAE8 00187480 */  sll        $t6, $t8, 18
    /* 106EC 8000FAEC 000E4403 */  sra        $t0, $t6, 16
    /* 106F0 8000FAF0 0328082A */  slt        $at, $t9, $t0
    /* 106F4 8000FAF4 1020015D */  beqz       $at, .L8001006C
    /* 106F8 8000FAF8 00000000 */   nop
    /* 106FC 8000FAFC 8FA40094 */  lw         $a0, 0x94($sp)
    /* 10700 8000FB00 8FB80090 */  lw         $t8, 0x90($sp)
    /* 10704 8000FB04 0087082A */  slt        $at, $a0, $a3
    /* 10708 8000FB08 10200158 */  beqz       $at, .L8001006C
    /* 1070C 8000FB0C 0302082A */   slt       $at, $t8, $v0
    /* 10710 8000FB10 14200156 */  bnez       $at, .L8001006C
    /* 10714 8000FB14 00000000 */   nop
    /* 10718 8000FB18 8FAE008C */  lw         $t6, 0x8C($sp)
    /* 1071C 8000FB1C 8FAF0098 */  lw         $t7, 0x98($sp)
    /* 10720 8000FB20 01C3082A */  slt        $at, $t6, $v1
    /* 10724 8000FB24 14200151 */  bnez       $at, .L8001006C
    /* 10728 8000FB28 01E2082A */   slt       $at, $t7, $v0
    /* 1072C 8000FB2C 10200017 */  beqz       $at, .L8000FB8C
    /* 10730 8000FB30 004FC823 */   subu      $t9, $v0, $t7
    /* 10734 8000FB34 8FAF0004 */  lw         $t7, 0x4($sp)
    /* 10738 8000FB38 0019C0C0 */  sll        $t8, $t9, 3
    /* 1073C 8000FB3C 00187140 */  sll        $t6, $t8, 5
    /* 10740 8000FB40 01CF001A */  div        $zero, $t6, $t7
    /* 10744 8000FB44 87B90076 */  lh         $t9, 0x76($sp)
    /* 10748 8000FB48 24D8FFFF */  addiu      $t8, $a2, -0x1
    /* 1074C 8000FB4C 15E00002 */  bnez       $t7, .L8000FB58
    /* 10750 8000FB50 00000000 */   nop
    /* 10754 8000FB54 0007000D */  break      7
  .L8000FB58:
    /* 10758 8000FB58 2401FFFF */  addiu      $at, $zero, -0x1
    /* 1075C 8000FB5C 15E10004 */  bne        $t7, $at, .L8000FB70
    /* 10760 8000FB60 3C018000 */   lui       $at, (0x80000000 >> 16)
    /* 10764 8000FB64 15C10002 */  bne        $t6, $at, .L8000FB70
    /* 10768 8000FB68 00000000 */   nop
    /* 1076C 8000FB6C 0006000D */  break      6
  .L8000FB70:
    /* 10770 8000FB70 00187140 */  sll        $t6, $t8, 5
    /* 10774 8000FB74 00002812 */  mflo       $a1
    /* 10778 8000FB78 00A06025 */  or         $t4, $a1, $zero
    /* 1077C 8000FB7C 172B0002 */  bne        $t9, $t3, .L8000FB88
    /* 10780 8000FB80 00000000 */   nop
    /* 10784 8000FB84 01C56023 */  subu       $t4, $t6, $a1
  .L8000FB88:
    /* 10788 8000FB88 AFA20098 */  sw         $v0, 0x98($sp)
  .L8000FB8C:
    /* 1078C 8000FB8C 0083082A */  slt        $at, $a0, $v1
    /* 10790 8000FB90 10200017 */  beqz       $at, .L8000FBF0
    /* 10794 8000FB94 00647823 */   subu      $t7, $v1, $a0
    /* 10798 8000FB98 8FAE0000 */  lw         $t6, 0x0($sp)
    /* 1079C 8000FB9C 000FC8C0 */  sll        $t9, $t7, 3
    /* 107A0 8000FBA0 0019C140 */  sll        $t8, $t9, 5
    /* 107A4 8000FBA4 030E001A */  div        $zero, $t8, $t6
    /* 107A8 8000FBA8 87AF0074 */  lh         $t7, 0x74($sp)
    /* 107AC 8000FBAC 2539FFFF */  addiu      $t9, $t1, -0x1
    /* 107B0 8000FBB0 15C00002 */  bnez       $t6, .L8000FBBC
    /* 107B4 8000FBB4 00000000 */   nop
    /* 107B8 8000FBB8 0007000D */  break      7
  .L8000FBBC:
    /* 107BC 8000FBBC 2401FFFF */  addiu      $at, $zero, -0x1
    /* 107C0 8000FBC0 15C10004 */  bne        $t6, $at, .L8000FBD4
    /* 107C4 8000FBC4 3C018000 */   lui       $at, (0x80000000 >> 16)
    /* 107C8 8000FBC8 17010002 */  bne        $t8, $at, .L8000FBD4
    /* 107CC 8000FBCC 00000000 */   nop
    /* 107D0 8000FBD0 0006000D */  break      6
  .L8000FBD4:
    /* 107D4 8000FBD4 0019C140 */  sll        $t8, $t9, 5
    /* 107D8 8000FBD8 00001012 */  mflo       $v0
    /* 107DC 8000FBDC 00406825 */  or         $t5, $v0, $zero
    /* 107E0 8000FBE0 15EB0002 */  bne        $t7, $t3, .L8000FBEC
    /* 107E4 8000FBE4 00000000 */   nop
    /* 107E8 8000FBE8 03026823 */  subu       $t5, $t8, $v0
  .L8000FBEC:
    /* 107EC 8000FBEC AFA30094 */  sw         $v1, 0x94($sp)
  .L8000FBF0:
    /* 107F0 8000FBF0 8FAE0090 */  lw         $t6, 0x90($sp)
    /* 107F4 8000FBF4 250FFFFC */  addiu      $t7, $t0, -0x4
    /* 107F8 8000FBF8 01C8082A */  slt        $at, $t6, $t0
    /* 107FC 8000FBFC 14200002 */  bnez       $at, .L8000FC08
    /* 10800 8000FC00 24F8FFFC */   addiu     $t8, $a3, -0x4
    /* 10804 8000FC04 AFAF0090 */  sw         $t7, 0x90($sp)
  .L8000FC08:
    /* 10808 8000FC08 8FB9008C */  lw         $t9, 0x8C($sp)
    /* 1080C 8000FC0C 3C038012 */  lui        $v1, %hi(gRegionAllocPtr)
    /* 10810 8000FC10 0327082A */  slt        $at, $t9, $a3
    /* 10814 8000FC14 14200002 */  bnez       $at, .L8000FC20
    /* 10818 8000FC18 24634830 */   addiu     $v1, $v1, %lo(gRegionAllocPtr)
    /* 1081C 8000FC1C AFB8008C */  sw         $t8, 0x8C($sp)
  .L8000FC20:
    /* 10820 8000FC20 93A200CB */  lbu        $v0, 0xCB($sp)
    /* 10824 8000FC24 3C18E700 */  lui        $t8, (0xE7000000 >> 16)
    /* 10828 8000FC28 14400006 */  bnez       $v0, .L8000FC44
    /* 1082C 8000FC2C 244FFFFF */   addiu     $t7, $v0, -0x1
    /* 10830 8000FC30 954E0004 */  lhu        $t6, 0x4($t2)
    /* 10834 8000FC34 AFAD0084 */  sw         $t5, 0x84($sp)
    /* 10838 8000FC38 AFAC0088 */  sw         $t4, 0x88($sp)
    /* 1083C 8000FC3C 10000004 */  b          .L8000FC50
    /* 10840 8000FC40 A7AE005C */   sh        $t6, 0x5C($sp)
  .L8000FC44:
    /* 10844 8000FC44 A7AF005C */  sh         $t7, 0x5C($sp)
    /* 10848 8000FC48 AFAC0088 */  sw         $t4, 0x88($sp)
    /* 1084C 8000FC4C AFAD0084 */  sw         $t5, 0x84($sp)
  .L8000FC50:
    /* 10850 8000FC50 97A800C6 */  lhu        $t0, 0xC6($sp)
    /* 10854 8000FC54 24010100 */  addiu      $at, $zero, 0x100
    /* 10858 8000FC58 1101001C */  beq        $t0, $at, .L8000FCCC
    /* 1085C 8000FC5C 01005825 */   or        $t3, $t0, $zero
    /* 10860 8000FC60 8C620000 */  lw         $v0, 0x0($v1)
    /* 10864 8000FC64 3C0FFC11 */  lui        $t7, (0xFC119623 >> 16)
    /* 10868 8000FC68 24590008 */  addiu      $t9, $v0, 0x8
    /* 1086C 8000FC6C AC790000 */  sw         $t9, 0x0($v1)
    /* 10870 8000FC70 AC400004 */  sw         $zero, 0x4($v0)
    /* 10874 8000FC74 AC580000 */  sw         $t8, 0x0($v0)
    /* 10878 8000FC78 8C620000 */  lw         $v0, 0x0($v1)
    /* 1087C 8000FC7C 3C19FF2F */  lui        $t9, (0xFF2FFFFF >> 16)
    /* 10880 8000FC80 244E0008 */  addiu      $t6, $v0, 0x8
    /* 10884 8000FC84 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10888 8000FC88 3739FFFF */  ori        $t9, $t9, (0xFF2FFFFF & 0xFFFF)
    /* 1088C 8000FC8C 35EF9623 */  ori        $t7, $t7, (0xFC119623 & 0xFFFF)
    /* 10890 8000FC90 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 10894 8000FC94 AC590004 */  sw         $t9, 0x4($v0)
    /* 10898 8000FC98 8C620000 */  lw         $v0, 0x0($v1)
    /* 1089C 8000FC9C 310400FF */  andi       $a0, $t0, 0xFF
    /* 108A0 8000FCA0 24580008 */  addiu      $t8, $v0, 0x8
    /* 108A4 8000FCA4 AC780000 */  sw         $t8, 0x0($v1)
    /* 108A8 8000FCA8 3C0EFA00 */  lui        $t6, (0xFA000000 >> 16)
    /* 108AC 8000FCAC 00047E00 */  sll        $t7, $a0, 24
    /* 108B0 8000FCB0 0004CC00 */  sll        $t9, $a0, 16
    /* 108B4 8000FCB4 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 108B8 8000FCB8 00047200 */  sll        $t6, $a0, 8
    /* 108BC 8000FCBC 01F9C025 */  or         $t8, $t7, $t9
    /* 108C0 8000FCC0 030E7825 */  or         $t7, $t8, $t6
    /* 108C4 8000FCC4 35F900FF */  ori        $t9, $t7, 0xFF
    /* 108C8 8000FCC8 AC590004 */  sw         $t9, 0x4($v0)
  .L8000FCCC:
    /* 108CC 8000FCCC 3C038012 */  lui        $v1, %hi(gRegionAllocPtr)
    /* 108D0 8000FCD0 24634830 */  addiu      $v1, $v1, %lo(gRegionAllocPtr)
    /* 108D4 8000FCD4 8C620000 */  lw         $v0, 0x0($v1)
    /* 108D8 8000FCD8 3C01FD48 */  lui        $at, (0xFD480000 >> 16)
    /* 108DC 8000FCDC 24580008 */  addiu      $t8, $v0, 0x8
    /* 108E0 8000FCE0 AC780000 */  sw         $t8, 0x0($v1)
    /* 108E4 8000FCE4 914E0006 */  lbu        $t6, 0x6($t2)
    /* 108E8 8000FCE8 00000000 */  nop
    /* 108EC 8000FCEC 000E7843 */  sra        $t7, $t6, 1
    /* 108F0 8000FCF0 25F9FFFF */  addiu      $t9, $t7, -0x1
    /* 108F4 8000FCF4 33380FFF */  andi       $t8, $t9, 0xFFF
    /* 108F8 8000FCF8 03017025 */  or         $t6, $t8, $at
    /* 108FC 8000FCFC AC4E0000 */  sw         $t6, 0x0($v0)
    /* 10900 8000FD00 8FB900B0 */  lw         $t9, 0xB0($sp)
    /* 10904 8000FD04 8D4F0000 */  lw         $t7, 0x0($t2)
    /* 10908 8000FD08 3C01F548 */  lui        $at, (0xF5480000 >> 16)
    /* 1090C 8000FD0C 01F9C021 */  addu       $t8, $t7, $t9
    /* 10910 8000FD10 AC580004 */  sw         $t8, 0x4($v0)
    /* 10914 8000FD14 8C620000 */  lw         $v0, 0x0($v1)
    /* 10918 8000FD18 00000000 */  nop
    /* 1091C 8000FD1C 244E0008 */  addiu      $t6, $v0, 0x8
    /* 10920 8000FD20 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10924 8000FD24 914F0006 */  lbu        $t7, 0x6($t2)
    /* 10928 8000FD28 00000000 */  nop
    /* 1092C 8000FD2C 25F90001 */  addiu      $t9, $t7, 0x1
    /* 10930 8000FD30 0019C043 */  sra        $t8, $t9, 1
    /* 10934 8000FD34 270E0007 */  addiu      $t6, $t8, 0x7
    /* 10938 8000FD38 000E78C3 */  sra        $t7, $t6, 3
    /* 1093C 8000FD3C 31F901FF */  andi       $t9, $t7, 0x1FF
    /* 10940 8000FD40 0019C240 */  sll        $t8, $t9, 9
    /* 10944 8000FD44 3C0F0708 */  lui        $t7, (0x7080200 >> 16)
    /* 10948 8000FD48 35EF0200 */  ori        $t7, $t7, (0x7080200 & 0xFFFF)
    /* 1094C 8000FD4C 03017025 */  or         $t6, $t8, $at
    /* 10950 8000FD50 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 10954 8000FD54 AC4F0004 */  sw         $t7, 0x4($v0)
    /* 10958 8000FD58 8C620000 */  lw         $v0, 0x0($v1)
    /* 1095C 8000FD5C 3C18E600 */  lui        $t8, (0xE6000000 >> 16)
    /* 10960 8000FD60 24590008 */  addiu      $t9, $v0, 0x8
    /* 10964 8000FD64 AC790000 */  sw         $t9, 0x0($v1)
    /* 10968 8000FD68 AC580000 */  sw         $t8, 0x0($v0)
    /* 1096C 8000FD6C AC400004 */  sw         $zero, 0x4($v0)
    /* 10970 8000FD70 8C620000 */  lw         $v0, 0x0($v1)
    /* 10974 8000FD74 3C0FF400 */  lui        $t7, (0xF4000000 >> 16)
    /* 10978 8000FD78 244E0008 */  addiu      $t6, $v0, 0x8
    /* 1097C 8000FD7C AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10980 8000FD80 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 10984 8000FD84 91590006 */  lbu        $t9, 0x6($t2)
    /* 10988 8000FD88 3C010700 */  lui        $at, (0x7000000 >> 16)
    /* 1098C 8000FD8C 0019C040 */  sll        $t8, $t9, 1
    /* 10990 8000FD90 330E0FFF */  andi       $t6, $t8, 0xFFF
    /* 10994 8000FD94 91580007 */  lbu        $t8, 0x7($t2)
    /* 10998 8000FD98 000E7B00 */  sll        $t7, $t6, 12
    /* 1099C 8000FD9C 01E1C825 */  or         $t9, $t7, $at
    /* 109A0 8000FDA0 00187080 */  sll        $t6, $t8, 2
    /* 109A4 8000FDA4 31CF0FFF */  andi       $t7, $t6, 0xFFF
    /* 109A8 8000FDA8 032FC025 */  or         $t8, $t9, $t7
    /* 109AC 8000FDAC AC580004 */  sw         $t8, 0x4($v0)
    /* 109B0 8000FDB0 8C620000 */  lw         $v0, 0x0($v1)
    /* 109B4 8000FDB4 3C19E700 */  lui        $t9, (0xE7000000 >> 16)
    /* 109B8 8000FDB8 244E0008 */  addiu      $t6, $v0, 0x8
    /* 109BC 8000FDBC AC6E0000 */  sw         $t6, 0x0($v1)
    /* 109C0 8000FDC0 AC590000 */  sw         $t9, 0x0($v0)
    /* 109C4 8000FDC4 AC400004 */  sw         $zero, 0x4($v0)
    /* 109C8 8000FDC8 8C620000 */  lw         $v0, 0x0($v1)
    /* 109CC 8000FDCC 3C01F540 */  lui        $at, (0xF5400000 >> 16)
    /* 109D0 8000FDD0 244F0008 */  addiu      $t7, $v0, 0x8
    /* 109D4 8000FDD4 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 109D8 8000FDD8 91580006 */  lbu        $t8, 0x6($t2)
    /* 109DC 8000FDDC 00000000 */  nop
    /* 109E0 8000FDE0 270E0001 */  addiu      $t6, $t8, 0x1
    /* 109E4 8000FDE4 000EC843 */  sra        $t9, $t6, 1
    /* 109E8 8000FDE8 272F0007 */  addiu      $t7, $t9, 0x7
    /* 109EC 8000FDEC 000FC0C3 */  sra        $t8, $t7, 3
    /* 109F0 8000FDF0 330E01FF */  andi       $t6, $t8, 0x1FF
    /* 109F4 8000FDF4 000ECA40 */  sll        $t9, $t6, 9
    /* 109F8 8000FDF8 3C180008 */  lui        $t8, (0x80200 >> 16)
    /* 109FC 8000FDFC 37180200 */  ori        $t8, $t8, (0x80200 & 0xFFFF)
    /* 10A00 8000FE00 03217825 */  or         $t7, $t9, $at
    /* 10A04 8000FE04 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 10A08 8000FE08 AC580004 */  sw         $t8, 0x4($v0)
    /* 10A0C 8000FE0C 8C620000 */  lw         $v0, 0x0($v1)
    /* 10A10 8000FE10 3C19F200 */  lui        $t9, (0xF2000000 >> 16)
    /* 10A14 8000FE14 244E0008 */  addiu      $t6, $v0, 0x8
    /* 10A18 8000FE18 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10A1C 8000FE1C AC590000 */  sw         $t9, 0x0($v0)
    /* 10A20 8000FE20 914F0006 */  lbu        $t7, 0x6($t2)
    /* 10A24 8000FE24 00406025 */  or         $t4, $v0, $zero
    /* 10A28 8000FE28 000FC080 */  sll        $t8, $t7, 2
    /* 10A2C 8000FE2C 914F0007 */  lbu        $t7, 0x7($t2)
    /* 10A30 8000FE30 330E0FFF */  andi       $t6, $t8, 0xFFF
    /* 10A34 8000FE34 000ECB00 */  sll        $t9, $t6, 12
    /* 10A38 8000FE38 000FC080 */  sll        $t8, $t7, 2
    /* 10A3C 8000FE3C 330E0FFF */  andi       $t6, $t8, 0xFFF
    /* 10A40 8000FE40 032E7825 */  or         $t7, $t9, $t6
    /* 10A44 8000FE44 AC4F0004 */  sw         $t7, 0x4($v0)
    /* 10A48 8000FE48 8C620000 */  lw         $v0, 0x0($v1)
    /* 10A4C 8000FE4C 3C19FD10 */  lui        $t9, (0xFD100000 >> 16)
    /* 10A50 8000FE50 24580008 */  addiu      $t8, $v0, 0x8
    /* 10A54 8000FE54 AC780000 */  sw         $t8, 0x0($v1)
    /* 10A58 8000FE58 AC590000 */  sw         $t9, 0x0($v0)
    /* 10A5C 8000FE5C 97AE005C */  lhu        $t6, 0x5C($sp)
    /* 10A60 8000FE60 8FB8009C */  lw         $t8, 0x9C($sp)
    /* 10A64 8000FE64 000E7940 */  sll        $t7, $t6, 5
    /* 10A68 8000FE68 01F8C821 */  addu       $t9, $t7, $t8
    /* 10A6C 8000FE6C AC590004 */  sw         $t9, 0x4($v0)
    /* 10A70 8000FE70 8C620000 */  lw         $v0, 0x0($v1)
    /* 10A74 8000FE74 3C0FE800 */  lui        $t7, (0xE8000000 >> 16)
    /* 10A78 8000FE78 244E0008 */  addiu      $t6, $v0, 0x8
    /* 10A7C 8000FE7C AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10A80 8000FE80 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 10A84 8000FE84 AC400004 */  sw         $zero, 0x4($v0)
    /* 10A88 8000FE88 8C620000 */  lw         $v0, 0x0($v1)
    /* 10A8C 8000FE8C 3C19F500 */  lui        $t9, (0xF5000100 >> 16)
    /* 10A90 8000FE90 24580008 */  addiu      $t8, $v0, 0x8
    /* 10A94 8000FE94 AC780000 */  sw         $t8, 0x0($v1)
    /* 10A98 8000FE98 37390100 */  ori        $t9, $t9, (0xF5000100 & 0xFFFF)
    /* 10A9C 8000FE9C 3C0E0700 */  lui        $t6, (0x7000000 >> 16)
    /* 10AA0 8000FEA0 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 10AA4 8000FEA4 AC590000 */  sw         $t9, 0x0($v0)
    /* 10AA8 8000FEA8 00403025 */  or         $a2, $v0, $zero
    /* 10AAC 8000FEAC 8C620000 */  lw         $v0, 0x0($v1)
    /* 10AB0 8000FEB0 3C18E600 */  lui        $t8, (0xE6000000 >> 16)
    /* 10AB4 8000FEB4 244F0008 */  addiu      $t7, $v0, 0x8
    /* 10AB8 8000FEB8 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 10ABC 8000FEBC AC580000 */  sw         $t8, 0x0($v0)
    /* 10AC0 8000FEC0 AC400004 */  sw         $zero, 0x4($v0)
    /* 10AC4 8000FEC4 00403825 */  or         $a3, $v0, $zero
    /* 10AC8 8000FEC8 8C620000 */  lw         $v0, 0x0($v1)
    /* 10ACC 8000FECC 3C0F0703 */  lui        $t7, (0x703C000 >> 16)
    /* 10AD0 8000FED0 24590008 */  addiu      $t9, $v0, 0x8
    /* 10AD4 8000FED4 AC790000 */  sw         $t9, 0x0($v1)
    /* 10AD8 8000FED8 35EFC000 */  ori        $t7, $t7, (0x703C000 & 0xFFFF)
    /* 10ADC 8000FEDC 3C0EF000 */  lui        $t6, (0xF0000000 >> 16)
    /* 10AE0 8000FEE0 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 10AE4 8000FEE4 AC4F0004 */  sw         $t7, 0x4($v0)
    /* 10AE8 8000FEE8 00404025 */  or         $t0, $v0, $zero
    /* 10AEC 8000FEEC 8C620000 */  lw         $v0, 0x0($v1)
    /* 10AF0 8000FEF0 3C19E700 */  lui        $t9, (0xE7000000 >> 16)
    /* 10AF4 8000FEF4 24580008 */  addiu      $t8, $v0, 0x8
    /* 10AF8 8000FEF8 AC780000 */  sw         $t8, 0x0($v1)
    /* 10AFC 8000FEFC AC590000 */  sw         $t9, 0x0($v0)
    /* 10B00 8000FF00 AC400004 */  sw         $zero, 0x4($v0)
    /* 10B04 8000FF04 8FAF0090 */  lw         $t7, 0x90($sp)
    /* 10B08 8000FF08 00404825 */  or         $t1, $v0, $zero
    /* 10B0C 8000FF0C 8C620000 */  lw         $v0, 0x0($v1)
    /* 10B10 8000FF10 31F80FFF */  andi       $t8, $t7, 0xFFF
    /* 10B14 8000FF14 8FAF008C */  lw         $t7, 0x8C($sp)
    /* 10B18 8000FF18 0018CB00 */  sll        $t9, $t8, 12
    /* 10B1C 8000FF1C 244E0008 */  addiu      $t6, $v0, 0x8
    /* 10B20 8000FF20 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 10B24 8000FF24 3C01E400 */  lui        $at, (0xE4000000 >> 16)
    /* 10B28 8000FF28 03217025 */  or         $t6, $t9, $at
    /* 10B2C 8000FF2C 31F80FFF */  andi       $t8, $t7, 0xFFF
    /* 10B30 8000FF30 01D8C825 */  or         $t9, $t6, $t8
    /* 10B34 8000FF34 AC590000 */  sw         $t9, 0x0($v0)
    /* 10B38 8000FF38 8FAF0098 */  lw         $t7, 0x98($sp)
    /* 10B3C 8000FF3C 8FB90094 */  lw         $t9, 0x94($sp)
    /* 10B40 8000FF40 31EE0FFF */  andi       $t6, $t7, 0xFFF
    /* 10B44 8000FF44 000EC300 */  sll        $t8, $t6, 12
    /* 10B48 8000FF48 332F0FFF */  andi       $t7, $t9, 0xFFF
    /* 10B4C 8000FF4C 030F7025 */  or         $t6, $t8, $t7
    /* 10B50 8000FF50 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 10B54 8000FF54 00406825 */  or         $t5, $v0, $zero
    /* 10B58 8000FF58 8C620000 */  lw         $v0, 0x0($v1)
    /* 10B5C 8000FF5C 3C18B400 */  lui        $t8, (0xB4000000 >> 16)
    /* 10B60 8000FF60 24590008 */  addiu      $t9, $v0, 0x8
    /* 10B64 8000FF64 AC790000 */  sw         $t9, 0x0($v1)
    /* 10B68 8000FF68 AC580000 */  sw         $t8, 0x0($v0)
    /* 10B6C 8000FF6C 8FB80084 */  lw         $t8, 0x84($sp)
    /* 10B70 8000FF70 8FAE0088 */  lw         $t6, 0x88($sp)
    /* 10B74 8000FF74 330FFFFF */  andi       $t7, $t8, 0xFFFF
    /* 10B78 8000FF78 000ECC00 */  sll        $t9, $t6, 16
    /* 10B7C 8000FF7C 032F7025 */  or         $t6, $t9, $t7
    /* 10B80 8000FF80 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 10B84 8000FF84 00402025 */  or         $a0, $v0, $zero
    /* 10B88 8000FF88 8C620000 */  lw         $v0, 0x0($v1)
    /* 10B8C 8000FF8C 3C19B300 */  lui        $t9, (0xB3000000 >> 16)
    /* 10B90 8000FF90 24580008 */  addiu      $t8, $v0, 0x8
    /* 10B94 8000FF94 AC780000 */  sw         $t8, 0x0($v1)
    /* 10B98 8000FF98 AC590000 */  sw         $t9, 0x0($v0)
    /* 10B9C 8000FF9C 8FAF0004 */  lw         $t7, 0x4($sp)
    /* 10BA0 8000FFA0 340E8000 */  ori        $t6, $zero, 0x8000
    /* 10BA4 8000FFA4 01CF001A */  div        $zero, $t6, $t7
    /* 10BA8 8000FFA8 00402825 */  or         $a1, $v0, $zero
    /* 10BAC 8000FFAC 15E00002 */  bnez       $t7, .L8000FFB8
    /* 10BB0 8000FFB0 00000000 */   nop
    /* 10BB4 8000FFB4 0007000D */  break      7
  .L8000FFB8:
    /* 10BB8 8000FFB8 2401FFFF */  addiu      $at, $zero, -0x1
    /* 10BBC 8000FFBC 15E10004 */  bne        $t7, $at, .L8000FFD0
    /* 10BC0 8000FFC0 3C018000 */   lui       $at, (0x80000000 >> 16)
    /* 10BC4 8000FFC4 15C10002 */  bne        $t6, $at, .L8000FFD0
    /* 10BC8 8000FFC8 00000000 */   nop
    /* 10BCC 8000FFCC 0006000D */  break      6
  .L8000FFD0:
    /* 10BD0 8000FFD0 87AE0076 */  lh         $t6, 0x76($sp)
    /* 10BD4 8000FFD4 8FAF0000 */  lw         $t7, 0x0($sp)
    /* 10BD8 8000FFD8 0000C012 */  mflo       $t8
    /* 10BDC 8000FFDC 3319FFFF */  andi       $t9, $t8, 0xFFFF
    /* 10BE0 8000FFE0 34188000 */  ori        $t8, $zero, 0x8000
    /* 10BE4 8000FFE4 032E0019 */  multu      $t9, $t6
    /* 10BE8 8000FFE8 0000C812 */  mflo       $t9
    /* 10BEC 8000FFEC 00197400 */  sll        $t6, $t9, 16
    /* 10BF0 8000FFF0 00000000 */  nop
    /* 10BF4 8000FFF4 030F001A */  div        $zero, $t8, $t7
    /* 10BF8 8000FFF8 15E00002 */  bnez       $t7, .L80010004
    /* 10BFC 8000FFFC 00000000 */   nop
    /* 10C00 80010000 0007000D */  break      7
  .L80010004:
    /* 10C04 80010004 2401FFFF */  addiu      $at, $zero, -0x1
    /* 10C08 80010008 15E10004 */  bne        $t7, $at, .L8001001C
    /* 10C0C 8001000C 3C018000 */   lui       $at, (0x80000000 >> 16)
    /* 10C10 80010010 17010002 */  bne        $t8, $at, .L8001001C
    /* 10C14 80010014 00000000 */   nop
    /* 10C18 80010018 0006000D */  break      6
  .L8001001C:
    /* 10C1C 8001001C 87AF0074 */  lh         $t7, 0x74($sp)
    /* 10C20 80010020 24010100 */  addiu      $at, $zero, 0x100
    /* 10C24 80010024 0000C812 */  mflo       $t9
    /* 10C28 80010028 3338FFFF */  andi       $t8, $t9, 0xFFFF
    /* 10C2C 8001002C 00000000 */  nop
    /* 10C30 80010030 030F0019 */  multu      $t8, $t7
    /* 10C34 80010034 0000C812 */  mflo       $t9
    /* 10C38 80010038 3338FFFF */  andi       $t8, $t9, 0xFFFF
    /* 10C3C 8001003C 330FFFFF */  andi       $t7, $t8, 0xFFFF
    /* 10C40 80010040 01CFC825 */  or         $t9, $t6, $t7
    /* 10C44 80010044 11610009 */  beq        $t3, $at, .L8001006C
    /* 10C48 80010048 ACB90004 */   sw        $t9, 0x4($a1)
    /* 10C4C 8001004C 8C620000 */  lw         $v0, 0x0($v1)
    /* 10C50 80010050 3C0F800E */  lui        $t7, %hi(gMenuRenderModeResetDl)
    /* 10C54 80010054 24580008 */  addiu      $t8, $v0, 0x8
    /* 10C58 80010058 AC780000 */  sw         $t8, 0x0($v1)
    /* 10C5C 8001005C 25EFEFF8 */  addiu      $t7, $t7, %lo(gMenuRenderModeResetDl)
    /* 10C60 80010060 3C0E0600 */  lui        $t6, (0x6000000 >> 16)
    /* 10C64 80010064 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 10C68 80010068 AC4F0004 */  sw         $t7, 0x4($v0)
  .L8001006C:
    /* 10C6C 8001006C 03E00008 */  jr         $ra
    /* 10C70 80010070 27BD00A8 */   addiu     $sp, $sp, 0xA8
endlabel drawMenuSpriteWithAlphaClipped
