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
nonmatching drawPulsingAssetTableSprite, 0x4D4

glabel drawPulsingAssetTableSprite
    /* 46A84 80045E84 27BDFFD0 */  addiu      $sp, $sp, -0x30
    /* 46A88 80045E88 AFB00004 */  sw         $s0, 0x4($sp)
    /* 46A8C 80045E8C AFA40030 */  sw         $a0, 0x30($sp)
    /* 46A90 80045E90 AFA50034 */  sw         $a1, 0x34($sp)
    /* 46A94 80045E94 AFA7003C */  sw         $a3, 0x3C($sp)
    /* 46A98 80045E98 8CCF0004 */  lw         $t7, 0x4($a2)
    /* 46A9C 80045E9C 00047400 */  sll        $t6, $a0, 16
    /* 46AA0 80045EA0 0005C400 */  sll        $t8, $a1, 16
    /* 46AA4 80045EA4 00182C03 */  sra        $a1, $t8, 16
    /* 46AA8 80045EA8 000E2403 */  sra        $a0, $t6, 16
    /* 46AAC 80045EAC 30EEFFFF */  andi       $t6, $a3, 0xFFFF
    /* 46AB0 80045EB0 000FC0C0 */  sll        $t8, $t7, 3
    /* 46AB4 80045EB4 01C03825 */  or         $a3, $t6, $zero
    /* 46AB8 80045EB8 0306C821 */  addu       $t9, $t8, $a2
    /* 46ABC 80045EBC 000778C0 */  sll        $t7, $a3, 3
    /* 46AC0 80045EC0 272E0008 */  addiu      $t6, $t9, 0x8
    /* 46AC4 80045EC4 00CF6021 */  addu       $t4, $a2, $t7
    /* 46AC8 80045EC8 3C088015 */  lui        $t0, %hi(gMenuViewportWidth)
    /* 46ACC 80045ECC 8508660A */  lh         $t0, %lo(gMenuViewportWidth)($t0)
    /* 46AD0 80045ED0 AFAE0024 */  sw         $t6, 0x24($sp)
    /* 46AD4 80045ED4 3C028015 */  lui        $v0, %hi(gMenuViewportCenterX)
    /* 46AD8 80045ED8 8442660E */  lh         $v0, %lo(gMenuViewportCenterX)($v0)
    /* 46ADC 80045EDC 3C038015 */  lui        $v1, %hi(gMenuViewportCenterY)
    /* 46AE0 80045EE0 84636610 */  lh         $v1, %lo(gMenuViewportCenterY)($v1)
    /* 46AE4 80045EE4 9198000E */  lbu        $t8, 0xE($t4)
    /* 46AE8 80045EE8 9199000F */  lbu        $t9, 0xF($t4)
    /* 46AEC 80045EEC 00826821 */  addu       $t5, $a0, $v0
    /* 46AF0 80045EF0 00A38021 */  addu       $s0, $a1, $v1
    /* 46AF4 80045EF4 03307021 */  addu       $t6, $t9, $s0
    /* 46AF8 80045EF8 258C0008 */  addiu      $t4, $t4, 0x8
    /* 46AFC 80045EFC AFAE0014 */  sw         $t6, 0x14($sp)
    /* 46B00 80045F00 AFA00010 */  sw         $zero, 0x10($sp)
    /* 46B04 80045F04 AFA0000C */  sw         $zero, 0xC($sp)
    /* 46B08 80045F08 030D5021 */  addu       $t2, $t8, $t5
    /* 46B0C 80045F0C 05010003 */  bgez       $t0, .L80045F1C
    /* 46B10 80045F10 00087843 */   sra       $t7, $t0, 1
    /* 46B14 80045F14 25010001 */  addiu      $at, $t0, 0x1
    /* 46B18 80045F18 00017843 */  sra        $t7, $at, 1
  .L80045F1C:
    /* 46B1C 80045F1C 004F4821 */  addu       $t1, $v0, $t7
    /* 46B20 80045F20 01A9082A */  slt        $at, $t5, $t1
    /* 46B24 80045F24 10200109 */  beqz       $at, .L8004634C
    /* 46B28 80045F28 01E04025 */   or        $t0, $t7, $zero
    /* 46B2C 80045F2C 3C058015 */  lui        $a1, %hi(gMenuViewportHeight)
    /* 46B30 80045F30 84A5660C */  lh         $a1, %lo(gMenuViewportHeight)($a1)
    /* 46B34 80045F34 00483823 */  subu       $a3, $v0, $t0
    /* 46B38 80045F38 04A10003 */  bgez       $a1, .L80045F48
    /* 46B3C 80045F3C 0005C043 */   sra       $t8, $a1, 1
    /* 46B40 80045F40 24A10001 */  addiu      $at, $a1, 0x1
    /* 46B44 80045F44 0001C043 */  sra        $t8, $at, 1
  .L80045F48:
    /* 46B48 80045F48 00782021 */  addu       $a0, $v1, $t8
    /* 46B4C 80045F4C 0204082A */  slt        $at, $s0, $a0
    /* 46B50 80045F50 102000FE */  beqz       $at, .L8004634C
    /* 46B54 80045F54 0147082A */   slt       $at, $t2, $a3
    /* 46B58 80045F58 142000FC */  bnez       $at, .L8004634C
    /* 46B5C 80045F5C AFAA0018 */   sw        $t2, 0x18($sp)
    /* 46B60 80045F60 8FA80014 */  lw         $t0, 0x14($sp)
    /* 46B64 80045F64 00781023 */  subu       $v0, $v1, $t8
    /* 46B68 80045F68 0102082A */  slt        $at, $t0, $v0
    /* 46B6C 80045F6C 142000F7 */  bnez       $at, .L8004634C
    /* 46B70 80045F70 3C038012 */   lui       $v1, %hi(gRegionAllocPtr)
    /* 46B74 80045F74 01A7082A */  slt        $at, $t5, $a3
    /* 46B78 80045F78 10200004 */  beqz       $at, .L80045F8C
    /* 46B7C 80045F7C 24634830 */   addiu     $v1, $v1, %lo(gRegionAllocPtr)
    /* 46B80 80045F80 00EDC823 */  subu       $t9, $a3, $t5
    /* 46B84 80045F84 AFB90010 */  sw         $t9, 0x10($sp)
    /* 46B88 80045F88 00E06825 */  or         $t5, $a3, $zero
  .L80045F8C:
    /* 46B8C 80045F8C 0202082A */  slt        $at, $s0, $v0
    /* 46B90 80045F90 10200003 */  beqz       $at, .L80045FA0
    /* 46B94 80045F94 00507023 */   subu      $t6, $v0, $s0
    /* 46B98 80045F98 AFAE000C */  sw         $t6, 0xC($sp)
    /* 46B9C 80045F9C 00408025 */  or         $s0, $v0, $zero
  .L80045FA0:
    /* 46BA0 80045FA0 8FAF0018 */  lw         $t7, 0x18($sp)
    /* 46BA4 80045FA4 24190020 */  addiu      $t9, $zero, 0x20
    /* 46BA8 80045FA8 01E9082A */  slt        $at, $t7, $t1
    /* 46BAC 80045FAC 14200003 */  bnez       $at, .L80045FBC
    /* 46BB0 80045FB0 0104082A */   slt       $at, $t0, $a0
    /* 46BB4 80045FB4 AFA90018 */  sw         $t1, 0x18($sp)
    /* 46BB8 80045FB8 0104082A */  slt        $at, $t0, $a0
  .L80045FBC:
    /* 46BBC 80045FBC 14200003 */  bnez       $at, .L80045FCC
    /* 46BC0 80045FC0 AFA60038 */   sw        $a2, 0x38($sp)
    /* 46BC4 80045FC4 AFA40014 */  sw         $a0, 0x14($sp)
    /* 46BC8 80045FC8 AFA60038 */  sw         $a2, 0x38($sp)
  .L80045FCC:
    /* 46BCC 80045FCC 3C048012 */  lui        $a0, %hi(gFrameCounter)
    /* 46BD0 80045FD0 848435B0 */  lh         $a0, %lo(gFrameCounter)($a0)
    /* 46BD4 80045FD4 00000000 */  nop
    /* 46BD8 80045FD8 3098001F */  andi       $t8, $a0, 0x1F
    /* 46BDC 80045FDC 2B010011 */  slti       $at, $t8, 0x11
    /* 46BE0 80045FE0 14200002 */  bnez       $at, .L80045FEC
    /* 46BE4 80045FE4 03002025 */   or        $a0, $t8, $zero
    /* 46BE8 80045FE8 03382023 */  subu       $a0, $t9, $t8
  .L80045FEC:
    /* 46BEC 80045FEC 00047100 */  sll        $t6, $a0, 4
    /* 46BF0 80045FF0 29C10100 */  slti       $at, $t6, 0x100
    /* 46BF4 80045FF4 14200002 */  bnez       $at, .L80046000
    /* 46BF8 80045FF8 01C02025 */   or        $a0, $t6, $zero
    /* 46BFC 80045FFC 240400FF */  addiu      $a0, $zero, 0xFF
  .L80046000:
    /* 46C00 80046000 8C620000 */  lw         $v0, 0x0($v1)
    /* 46C04 80046004 3C18E700 */  lui        $t8, (0xE7000000 >> 16)
    /* 46C08 80046008 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46C0C 8004600C AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46C10 80046010 AC400004 */  sw         $zero, 0x4($v0)
    /* 46C14 80046014 AC580000 */  sw         $t8, 0x0($v0)
    /* 46C18 80046018 8C620000 */  lw         $v0, 0x0($v1)
    /* 46C1C 8004601C 3C0FFF2F */  lui        $t7, (0xFF2FFFFF >> 16)
    /* 46C20 80046020 24590008 */  addiu      $t9, $v0, 0x8
    /* 46C24 80046024 AC790000 */  sw         $t9, 0x0($v1)
    /* 46C28 80046028 3C0EFC11 */  lui        $t6, (0xFC119623 >> 16)
    /* 46C2C 8004602C 35CE9623 */  ori        $t6, $t6, (0xFC119623 & 0xFFFF)
    /* 46C30 80046030 35EFFFFF */  ori        $t7, $t7, (0xFF2FFFFF & 0xFFFF)
    /* 46C34 80046034 AC4F0004 */  sw         $t7, 0x4($v0)
    /* 46C38 80046038 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 46C3C 8004603C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46C40 80046040 308E00FF */  andi       $t6, $a0, 0xFF
    /* 46C44 80046044 24580008 */  addiu      $t8, $v0, 0x8
    /* 46C48 80046048 AC780000 */  sw         $t8, 0x0($v1)
    /* 46C4C 8004604C 000E7A00 */  sll        $t7, $t6, 8
    /* 46C50 80046050 3C19FA00 */  lui        $t9, (0xFA000000 >> 16)
    /* 46C54 80046054 3C01FFFF */  lui        $at, (0xFFFF00FF >> 16)
    /* 46C58 80046058 01E1C025 */  or         $t8, $t7, $at
    /* 46C5C 8004605C AC590000 */  sw         $t9, 0x0($v0)
    /* 46C60 80046060 371900FF */  ori        $t9, $t8, (0xFFFF00FF & 0xFFFF)
    /* 46C64 80046064 AC590004 */  sw         $t9, 0x4($v0)
    /* 46C68 80046068 8C620000 */  lw         $v0, 0x0($v1)
    /* 46C6C 8004606C 3C01FD48 */  lui        $at, (0xFD480000 >> 16)
    /* 46C70 80046070 244E0008 */  addiu      $t6, $v0, 0x8
    /* 46C74 80046074 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 46C78 80046078 918F0006 */  lbu        $t7, 0x6($t4)
    /* 46C7C 8004607C 00000000 */  nop
    /* 46C80 80046080 000FC043 */  sra        $t8, $t7, 1
    /* 46C84 80046084 2719FFFF */  addiu      $t9, $t8, -0x1
    /* 46C88 80046088 332E0FFF */  andi       $t6, $t9, 0xFFF
    /* 46C8C 8004608C 01C17825 */  or         $t7, $t6, $at
    /* 46C90 80046090 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 46C94 80046094 8FB90038 */  lw         $t9, 0x38($sp)
    /* 46C98 80046098 8D980000 */  lw         $t8, 0x0($t4)
    /* 46C9C 8004609C 3C01F548 */  lui        $at, (0xF5480000 >> 16)
    /* 46CA0 800460A0 03197021 */  addu       $t6, $t8, $t9
    /* 46CA4 800460A4 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 46CA8 800460A8 8C620000 */  lw         $v0, 0x0($v1)
    /* 46CAC 800460AC 00000000 */  nop
    /* 46CB0 800460B0 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46CB4 800460B4 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46CB8 800460B8 91980006 */  lbu        $t8, 0x6($t4)
    /* 46CBC 800460BC 00000000 */  nop
    /* 46CC0 800460C0 27190001 */  addiu      $t9, $t8, 0x1
    /* 46CC4 800460C4 00197043 */  sra        $t6, $t9, 1
    /* 46CC8 800460C8 25CF0007 */  addiu      $t7, $t6, 0x7
    /* 46CCC 800460CC 000FC0C3 */  sra        $t8, $t7, 3
    /* 46CD0 800460D0 331901FF */  andi       $t9, $t8, 0x1FF
    /* 46CD4 800460D4 00197240 */  sll        $t6, $t9, 9
    /* 46CD8 800460D8 3C180708 */  lui        $t8, (0x7080200 >> 16)
    /* 46CDC 800460DC 37180200 */  ori        $t8, $t8, (0x7080200 & 0xFFFF)
    /* 46CE0 800460E0 01C17825 */  or         $t7, $t6, $at
    /* 46CE4 800460E4 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 46CE8 800460E8 AC580004 */  sw         $t8, 0x4($v0)
    /* 46CEC 800460EC 8C620000 */  lw         $v0, 0x0($v1)
    /* 46CF0 800460F0 3C0EE600 */  lui        $t6, (0xE6000000 >> 16)
    /* 46CF4 800460F4 24590008 */  addiu      $t9, $v0, 0x8
    /* 46CF8 800460F8 AC790000 */  sw         $t9, 0x0($v1)
    /* 46CFC 800460FC AC400004 */  sw         $zero, 0x4($v0)
    /* 46D00 80046100 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 46D04 80046104 8C620000 */  lw         $v0, 0x0($v1)
    /* 46D08 80046108 3C18F400 */  lui        $t8, (0xF4000000 >> 16)
    /* 46D0C 8004610C 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46D10 80046110 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46D14 80046114 AC580000 */  sw         $t8, 0x0($v0)
    /* 46D18 80046118 91990006 */  lbu        $t9, 0x6($t4)
    /* 46D1C 8004611C 3C010700 */  lui        $at, (0x7000000 >> 16)
    /* 46D20 80046120 00197040 */  sll        $t6, $t9, 1
    /* 46D24 80046124 31CF0FFF */  andi       $t7, $t6, 0xFFF
    /* 46D28 80046128 918E0007 */  lbu        $t6, 0x7($t4)
    /* 46D2C 8004612C 000FC300 */  sll        $t8, $t7, 12
    /* 46D30 80046130 0301C825 */  or         $t9, $t8, $at
    /* 46D34 80046134 000E7880 */  sll        $t7, $t6, 2
    /* 46D38 80046138 31F80FFF */  andi       $t8, $t7, 0xFFF
    /* 46D3C 8004613C 03387025 */  or         $t6, $t9, $t8
    /* 46D40 80046140 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 46D44 80046144 8C620000 */  lw         $v0, 0x0($v1)
    /* 46D48 80046148 3C19E700 */  lui        $t9, (0xE7000000 >> 16)
    /* 46D4C 8004614C 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46D50 80046150 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46D54 80046154 AC400004 */  sw         $zero, 0x4($v0)
    /* 46D58 80046158 AC590000 */  sw         $t9, 0x0($v0)
    /* 46D5C 8004615C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46D60 80046160 3C01F540 */  lui        $at, (0xF5400000 >> 16)
    /* 46D64 80046164 24580008 */  addiu      $t8, $v0, 0x8
    /* 46D68 80046168 AC780000 */  sw         $t8, 0x0($v1)
    /* 46D6C 8004616C 918E0006 */  lbu        $t6, 0x6($t4)
    /* 46D70 80046170 00000000 */  nop
    /* 46D74 80046174 25CF0001 */  addiu      $t7, $t6, 0x1
    /* 46D78 80046178 000FC843 */  sra        $t9, $t7, 1
    /* 46D7C 8004617C 27380007 */  addiu      $t8, $t9, 0x7
    /* 46D80 80046180 001870C3 */  sra        $t6, $t8, 3
    /* 46D84 80046184 31CF01FF */  andi       $t7, $t6, 0x1FF
    /* 46D88 80046188 000FCA40 */  sll        $t9, $t7, 9
    /* 46D8C 8004618C 3C0E0008 */  lui        $t6, (0x80200 >> 16)
    /* 46D90 80046190 35CE0200 */  ori        $t6, $t6, (0x80200 & 0xFFFF)
    /* 46D94 80046194 0321C025 */  or         $t8, $t9, $at
    /* 46D98 80046198 AC580000 */  sw         $t8, 0x0($v0)
    /* 46D9C 8004619C AC4E0004 */  sw         $t6, 0x4($v0)
    /* 46DA0 800461A0 8C620000 */  lw         $v0, 0x0($v1)
    /* 46DA4 800461A4 3C19F200 */  lui        $t9, (0xF2000000 >> 16)
    /* 46DA8 800461A8 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46DAC 800461AC AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46DB0 800461B0 AC590000 */  sw         $t9, 0x0($v0)
    /* 46DB4 800461B4 91980006 */  lbu        $t8, 0x6($t4)
    /* 46DB8 800461B8 3C01E400 */  lui        $at, (0xE4000000 >> 16)
    /* 46DBC 800461BC 00187080 */  sll        $t6, $t8, 2
    /* 46DC0 800461C0 91980007 */  lbu        $t8, 0x7($t4)
    /* 46DC4 800461C4 31CF0FFF */  andi       $t7, $t6, 0xFFF
    /* 46DC8 800461C8 000FCB00 */  sll        $t9, $t7, 12
    /* 46DCC 800461CC 00187080 */  sll        $t6, $t8, 2
    /* 46DD0 800461D0 31CF0FFF */  andi       $t7, $t6, 0xFFF
    /* 46DD4 800461D4 032FC025 */  or         $t8, $t9, $t7
    /* 46DD8 800461D8 AC580004 */  sw         $t8, 0x4($v0)
    /* 46DDC 800461DC 8C620000 */  lw         $v0, 0x0($v1)
    /* 46DE0 800461E0 3C19FD10 */  lui        $t9, (0xFD100000 >> 16)
    /* 46DE4 800461E4 244E0008 */  addiu      $t6, $v0, 0x8
    /* 46DE8 800461E8 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 46DEC 800461EC AC590000 */  sw         $t9, 0x0($v0)
    /* 46DF0 800461F0 958F0004 */  lhu        $t7, 0x4($t4)
    /* 46DF4 800461F4 8FAE0024 */  lw         $t6, 0x24($sp)
    /* 46DF8 800461F8 000FC140 */  sll        $t8, $t7, 5
    /* 46DFC 800461FC 030EC821 */  addu       $t9, $t8, $t6
    /* 46E00 80046200 AC590004 */  sw         $t9, 0x4($v0)
    /* 46E04 80046204 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E08 80046208 3C18E800 */  lui        $t8, (0xE8000000 >> 16)
    /* 46E0C 8004620C 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46E10 80046210 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46E14 80046214 AC400004 */  sw         $zero, 0x4($v0)
    /* 46E18 80046218 AC580000 */  sw         $t8, 0x0($v0)
    /* 46E1C 8004621C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E20 80046220 3C19F500 */  lui        $t9, (0xF5000100 >> 16)
    /* 46E24 80046224 244E0008 */  addiu      $t6, $v0, 0x8
    /* 46E28 80046228 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 46E2C 8004622C 37390100 */  ori        $t9, $t9, (0xF5000100 & 0xFFFF)
    /* 46E30 80046230 3C0F0700 */  lui        $t7, (0x7000000 >> 16)
    /* 46E34 80046234 AC4F0004 */  sw         $t7, 0x4($v0)
    /* 46E38 80046238 AC590000 */  sw         $t9, 0x0($v0)
    /* 46E3C 8004623C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E40 80046240 3C0EE600 */  lui        $t6, (0xE6000000 >> 16)
    /* 46E44 80046244 24580008 */  addiu      $t8, $v0, 0x8
    /* 46E48 80046248 AC780000 */  sw         $t8, 0x0($v1)
    /* 46E4C 8004624C AC400004 */  sw         $zero, 0x4($v0)
    /* 46E50 80046250 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 46E54 80046254 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E58 80046258 3C180703 */  lui        $t8, (0x703C000 >> 16)
    /* 46E5C 8004625C 24590008 */  addiu      $t9, $v0, 0x8
    /* 46E60 80046260 AC790000 */  sw         $t9, 0x0($v1)
    /* 46E64 80046264 3718C000 */  ori        $t8, $t8, (0x703C000 & 0xFFFF)
    /* 46E68 80046268 3C0FF000 */  lui        $t7, (0xF0000000 >> 16)
    /* 46E6C 8004626C AC4F0000 */  sw         $t7, 0x0($v0)
    /* 46E70 80046270 AC580004 */  sw         $t8, 0x4($v0)
    /* 46E74 80046274 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E78 80046278 3C19E700 */  lui        $t9, (0xE7000000 >> 16)
    /* 46E7C 8004627C 244E0008 */  addiu      $t6, $v0, 0x8
    /* 46E80 80046280 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 46E84 80046284 AC400004 */  sw         $zero, 0x4($v0)
    /* 46E88 80046288 AC590000 */  sw         $t9, 0x0($v0)
    /* 46E8C 8004628C 8FB80018 */  lw         $t8, 0x18($sp)
    /* 46E90 80046290 8C620000 */  lw         $v0, 0x0($v1)
    /* 46E94 80046294 00187080 */  sll        $t6, $t8, 2
    /* 46E98 80046298 31D90FFF */  andi       $t9, $t6, 0xFFF
    /* 46E9C 8004629C 244F0008 */  addiu      $t7, $v0, 0x8
    /* 46EA0 800462A0 AC6F0000 */  sw         $t7, 0x0($v1)
    /* 46EA4 800462A4 8FAE0014 */  lw         $t6, 0x14($sp)
    /* 46EA8 800462A8 00197B00 */  sll        $t7, $t9, 12
    /* 46EAC 800462AC 01E1C025 */  or         $t8, $t7, $at
    /* 46EB0 800462B0 000EC880 */  sll        $t9, $t6, 2
    /* 46EB4 800462B4 332F0FFF */  andi       $t7, $t9, 0xFFF
    /* 46EB8 800462B8 030F7025 */  or         $t6, $t8, $t7
    /* 46EBC 800462BC 000DC880 */  sll        $t9, $t5, 2
    /* 46EC0 800462C0 33380FFF */  andi       $t8, $t9, 0xFFF
    /* 46EC4 800462C4 AC4E0000 */  sw         $t6, 0x0($v0)
    /* 46EC8 800462C8 00107080 */  sll        $t6, $s0, 2
    /* 46ECC 800462CC 31D90FFF */  andi       $t9, $t6, 0xFFF
    /* 46ED0 800462D0 00187B00 */  sll        $t7, $t8, 12
    /* 46ED4 800462D4 01F9C025 */  or         $t8, $t7, $t9
    /* 46ED8 800462D8 AC580004 */  sw         $t8, 0x4($v0)
    /* 46EDC 800462DC 8C620000 */  lw         $v0, 0x0($v1)
    /* 46EE0 800462E0 3C0FB400 */  lui        $t7, (0xB4000000 >> 16)
    /* 46EE4 800462E4 244E0008 */  addiu      $t6, $v0, 0x8
    /* 46EE8 800462E8 AC6E0000 */  sw         $t6, 0x0($v1)
    /* 46EEC 800462EC AC4F0000 */  sw         $t7, 0x0($v0)
    /* 46EF0 800462F0 8FB9000C */  lw         $t9, 0xC($sp)
    /* 46EF4 800462F4 8FAE0010 */  lw         $t6, 0x10($sp)
    /* 46EF8 800462F8 0019C140 */  sll        $t8, $t9, 5
    /* 46EFC 800462FC 000E7D40 */  sll        $t7, $t6, 21
    /* 46F00 80046300 330EFFFF */  andi       $t6, $t8, 0xFFFF
    /* 46F04 80046304 01EEC825 */  or         $t9, $t7, $t6
    /* 46F08 80046308 AC590004 */  sw         $t9, 0x4($v0)
    /* 46F0C 8004630C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46F10 80046310 3C0E0400 */  lui        $t6, (0x4000400 >> 16)
    /* 46F14 80046314 24580008 */  addiu      $t8, $v0, 0x8
    /* 46F18 80046318 AC780000 */  sw         $t8, 0x0($v1)
    /* 46F1C 8004631C 35CE0400 */  ori        $t6, $t6, (0x4000400 & 0xFFFF)
    /* 46F20 80046320 3C0FB300 */  lui        $t7, (0xB3000000 >> 16)
    /* 46F24 80046324 AC4F0000 */  sw         $t7, 0x0($v0)
    /* 46F28 80046328 AC4E0004 */  sw         $t6, 0x4($v0)
    /* 46F2C 8004632C 8C620000 */  lw         $v0, 0x0($v1)
    /* 46F30 80046330 3C0F800E */  lui        $t7, %hi(gMenuRenderModeResetDl)
    /* 46F34 80046334 24590008 */  addiu      $t9, $v0, 0x8
    /* 46F38 80046338 AC790000 */  sw         $t9, 0x0($v1)
    /* 46F3C 8004633C 25EFEFF8 */  addiu      $t7, $t7, %lo(gMenuRenderModeResetDl)
    /* 46F40 80046340 3C180600 */  lui        $t8, (0x6000000 >> 16)
    /* 46F44 80046344 AC580000 */  sw         $t8, 0x0($v0)
    /* 46F48 80046348 AC4F0004 */  sw         $t7, 0x4($v0)
  .L8004634C:
    /* 46F4C 8004634C 8FB00004 */  lw         $s0, 0x4($sp)
    /* 46F50 80046350 03E00008 */  jr         $ra
    /* 46F54 80046354 27BD0030 */   addiu     $sp, $sp, 0x30
endlabel drawPulsingAssetTableSprite
