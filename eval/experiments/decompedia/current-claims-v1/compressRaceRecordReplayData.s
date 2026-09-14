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
nonmatching compressRaceRecordReplayData, 0x114

glabel compressRaceRecordReplayData
    /* 41880 80040C80 27BDFFF0 */  addiu      $sp, $sp, -0x10
    /* 41884 80040C84 AFB00008 */  sw         $s0, 0x8($sp)
    /* 41888 80040C88 AFB1000C */  sw         $s1, 0xC($sp)
    /* 4188C 80040C8C 00808025 */  or         $s0, $a0, $zero
    /* 41890 80040C90 00001025 */  or         $v0, $zero, $zero
    /* 41894 80040C94 A4C50000 */  sh         $a1, 0x0($a2)
    /* 41898 80040C98 24030001 */  addiu      $v1, $zero, 0x1
    /* 4189C 80040C9C 00A03825 */  or         $a3, $a1, $zero
    /* 418A0 80040CA0 00004025 */  or         $t0, $zero, $zero
    /* 418A4 80040CA4 00004825 */  or         $t1, $zero, $zero
    /* 418A8 80040CA8 240A0001 */  addiu      $t2, $zero, 0x1
    /* 418AC 80040CAC 24CD0002 */  addiu      $t5, $a2, 0x2
  .L80040CB0:
    /* 418B0 80040CB0 28E10040 */  slti       $at, $a3, 0x40
    /* 418B4 80040CB4 14200002 */  bnez       $at, .L80040CC0
    /* 418B8 80040CB8 00E02025 */   or        $a0, $a3, $zero
    /* 418BC 80040CBC 2404003F */  addiu      $a0, $zero, 0x3F
  .L80040CC0:
    /* 418C0 80040CC0 2446FFFF */  addiu      $a2, $v0, -0x1
  .L80040CC4:
    /* 418C4 80040CC4 04C00016 */  bltz       $a2, .L80040D20
    /* 418C8 80040CC8 00003825 */   or        $a3, $zero, $zero
    /* 418CC 80040CCC 1880000B */  blez       $a0, .L80040CFC
    /* 418D0 80040CD0 00005825 */   or        $t3, $zero, $zero
    /* 418D4 80040CD4 02026021 */  addu       $t4, $s0, $v0
    /* 418D8 80040CD8 00D08821 */  addu       $s1, $a2, $s0
  .L80040CDC:
    /* 418DC 80040CDC 918E0000 */  lbu        $t6, 0x0($t4)
    /* 418E0 80040CE0 922F0000 */  lbu        $t7, 0x0($s1)
    /* 418E4 80040CE4 256B0001 */  addiu      $t3, $t3, 0x1
    /* 418E8 80040CE8 15CF0004 */  bne        $t6, $t7, .L80040CFC
    /* 418EC 80040CEC 258C0001 */   addiu     $t4, $t4, 0x1
    /* 418F0 80040CF0 26310001 */  addiu      $s1, $s1, 0x1
    /* 418F4 80040CF4 1564FFF9 */  bne        $t3, $a0, .L80040CDC
    /* 418F8 80040CF8 24E70001 */   addiu     $a3, $a3, 0x1
  .L80040CFC:
    /* 418FC 80040CFC 0127082A */  slt        $at, $t1, $a3
    /* 41900 80040D00 10200003 */  beqz       $at, .L80040D10
    /* 41904 80040D04 00000000 */   nop
    /* 41908 80040D08 01404025 */  or         $t0, $t2, $zero
    /* 4190C 80040D0C 00E04825 */  or         $t1, $a3, $zero
  .L80040D10:
    /* 41910 80040D10 254A0001 */  addiu      $t2, $t2, 0x1
    /* 41914 80040D14 29410400 */  slti       $at, $t2, 0x400
    /* 41918 80040D18 1420FFEA */  bnez       $at, .L80040CC4
    /* 4191C 80040D1C 24C6FFFF */   addiu     $a2, $a2, -0x1
  .L80040D20:
    /* 41920 80040D20 1D200008 */  bgtz       $t1, .L80040D44
    /* 41924 80040D24 240A0001 */   addiu     $t2, $zero, 0x1
    /* 41928 80040D28 0202C021 */  addu       $t8, $s0, $v0
    /* 4192C 80040D2C 93190000 */  lbu        $t9, 0x0($t8)
    /* 41930 80040D30 24630001 */  addiu      $v1, $v1, 0x1
    /* 41934 80040D34 25AD0002 */  addiu      $t5, $t5, 0x2
    /* 41938 80040D38 24420001 */  addiu      $v0, $v0, 0x1
    /* 4193C 80040D3C 10000007 */  b          .L80040D5C
    /* 41940 80040D40 A5B9FFFE */   sh        $t9, -0x2($t5)
  .L80040D44:
    /* 41944 80040D44 00097280 */  sll        $t6, $t1, 10
    /* 41948 80040D48 01C87825 */  or         $t7, $t6, $t0
    /* 4194C 80040D4C A5AF0000 */  sh         $t7, 0x0($t5)
    /* 41950 80040D50 24630001 */  addiu      $v1, $v1, 0x1
    /* 41954 80040D54 25AD0002 */  addiu      $t5, $t5, 0x2
    /* 41958 80040D58 00491021 */  addu       $v0, $v0, $t1
  .L80040D5C:
    /* 4195C 80040D5C 10A20008 */  beq        $a1, $v0, .L80040D80
    /* 41960 80040D60 28611000 */   slti      $at, $v1, 0x1000
    /* 41964 80040D64 14200003 */  bnez       $at, .L80040D74
    /* 41968 80040D68 00A23823 */   subu      $a3, $a1, $v0
    /* 4196C 80040D6C 10000005 */  b          .L80040D84
    /* 41970 80040D70 2402FFFF */   addiu     $v0, $zero, -0x1
  .L80040D74:
    /* 41974 80040D74 00004025 */  or         $t0, $zero, $zero
    /* 41978 80040D78 1000FFCD */  b          .L80040CB0
    /* 4197C 80040D7C 00004825 */   or        $t1, $zero, $zero
  .L80040D80:
    /* 41980 80040D80 00601025 */  or         $v0, $v1, $zero
  .L80040D84:
    /* 41984 80040D84 8FB00008 */  lw         $s0, 0x8($sp)
    /* 41988 80040D88 8FB1000C */  lw         $s1, 0xC($sp)
    /* 4198C 80040D8C 03E00008 */  jr         $ra
    /* 41990 80040D90 27BD0010 */   addiu     $sp, $sp, 0x10
endlabel compressRaceRecordReplayData
