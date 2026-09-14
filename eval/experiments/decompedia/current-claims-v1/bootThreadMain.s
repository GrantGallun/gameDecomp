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
nonmatching bootThreadMain, 0x94

glabel bootThreadMain
    /* 9A2FC 800996FC 27BDFFE0 */  addiu      $sp, $sp, -0x20
    /* 9A300 80099700 AFBF001C */  sw         $ra, 0x1C($sp)
    /* 9A304 80099704 AFA40020 */  sw         $a0, 0x20($sp)
    /* 9A308 80099708 3C058012 */  lui        $a1, %hi(gPiManagerQueue)
    /* 9A30C 8009970C 3C068012 */  lui        $a2, %hi(gPiManagerMessages)
    /* 9A310 80099710 24C63CD8 */  addiu      $a2, $a2, %lo(gPiManagerMessages)
    /* 9A314 80099714 24A53CC0 */  addiu      $a1, $a1, %lo(gPiManagerQueue)
    /* 9A318 80099718 24040096 */  addiu      $a0, $zero, 0x96
    /* 9A31C 8009971C 0C028FB8 */  jal        osCreatePiManager
    /* 9A320 80099720 240700C8 */   addiu     $a3, $zero, 0xC8
    /* 9A324 80099724 3C0E8033 */  lui        $t6, %hi(D_80328480)
    /* 9A328 80099728 25CE8480 */  addiu      $t6, $t6, %lo(D_80328480)
    /* 9A32C 8009972C 3C048012 */  lui        $a0, %hi(gGameThread)
    /* 9A330 80099730 3C06800A */  lui        $a2, %hi(gameThreadMain)
    /* 9A334 80099734 8FA70020 */  lw         $a3, 0x20($sp)
    /* 9A338 80099738 240F000A */  addiu      $t7, $zero, 0xA
    /* 9A33C 8009973C AFAF0014 */  sw         $t7, 0x14($sp)
    /* 9A340 80099740 24C698E4 */  addiu      $a2, $a2, %lo(gameThreadMain)
    /* 9A344 80099744 24843960 */  addiu      $a0, $a0, %lo(gGameThread)
    /* 9A348 80099748 AFAE0010 */  sw         $t6, 0x10($sp)
    /* 9A34C 8009974C 0C0281BC */  jal        osCreateThread
    /* 9A350 80099750 24050002 */   addiu     $a1, $zero, 0x2
    /* 9A354 80099754 3C048012 */  lui        $a0, %hi(gGameThread)
    /* 9A358 80099758 0C028210 */  jal        osStartThread
    /* 9A35C 8009975C 24843960 */   addiu     $a0, $a0, %lo(gGameThread)
    /* 9A360 80099760 00002025 */  or         $a0, $zero, $zero
    /* 9A364 80099764 0C02901C */  jal        osSetThreadPri
    /* 9A368 80099768 00002825 */   or        $a1, $zero, $zero
  .L8009976C:
    /* 9A36C 8009976C 1000FFFF */  b          .L8009976C
    /* 9A370 80099770 00000000 */   nop
    /* 9A374 80099774 00000000 */  nop
    /* 9A378 80099778 00000000 */  nop
    /* 9A37C 8009977C 00000000 */  nop
    /* 9A380 80099780 8FBF001C */  lw         $ra, 0x1C($sp)
    /* 9A384 80099784 27BD0020 */  addiu      $sp, $sp, 0x20
    /* 9A388 80099788 03E00008 */  jr         $ra
    /* 9A38C 8009978C 00000000 */   nop
endlabel bootThreadMain
