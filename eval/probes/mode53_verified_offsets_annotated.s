
/mnt/c/Code/gameDecomp/eval/probes/mode53_verified_offsets.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <updateRacePlayerMode53AerialTrick>:
updateRacePlayerMode53AerialTrick():
   0:	27bdffe0 	addiu	sp,sp,-32
   4:	afbf001c 	sw	ra,28(sp)
   8:	afb00018 	sw	s0,24(sp)
   c:	848e0302 	lh	t6,770(a0)
  10:	00808025 	move	s0,a0
  14:	15c00010 	bnez	t6,58 <updateRacePlayerMode53AerialTrick+0x58>
  18:	00000000 	nop
  1c:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			1c: R_MIPS_26	setRaceMotionAnimation
  20:	24050004 	li	a1,4
  24:	860f0302 	lh	t7,770(s0)
  28:	8e1902fc 	lw	t9,764(s0)
  2c:	25f80001 	addiu	t8,t7,1
  30:	37280200 	ori	t0,t9,0x200
  34:	a6180302 	sh	t8,770(s0)
  38:	ae0802fc 	sw	t0,764(s0)
  3c:	ae00007c 	sw	zero,124(s0)
  40:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			40: R_MIPS_26	resetRacePlayerTrickSubstate
  44:	02002025 	move	a0,s0
  48:	02002025 	move	a0,s0
  4c:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			4c: R_MIPS_26	setRaceMotionAnimation
  50:	24050019 	li	a1,25
  54:	a6000304 	sh	zero,772(s0)
  58:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			58: R_MIPS_26	stepRaceMotionAnimationUntilEnd
  5c:	02002025 	move	a0,s0
  60:	8e050254 	lw	a1,596(s0)
  64:	02002025 	move	a0,s0
  68:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			68: R_MIPS_26	updateRacePlayerLeanAngle
  6c:	00003025 	move	a2,zero
  70:	8e090044 	lw	t1,68(s0)
  74:	8e0a0264 	lw	t2,612(s0)
  78:	26040040 	addiu	a0,s0,64
  7c:	012a5823 	subu	t3,t1,t2
  80:	ae0b0044 	sw	t3,68(s0)
  84:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			84: R_MIPS_26	clampRacePlayerVectorXZSpeed
  88:	02002825 	move	a1,s0
  8c:	8e020044 	lw	v0,68(s0)
  90:	8e0c001c 	lw	t4,28(s0)
  94:	8e0d0040 	lw	t5,64(s0)
  98:	8e0f0020 	lw	t7,32(s0)
  9c:	8e190024 	lw	t9,36(s0)
  a0:	8e080048 	lw	t0,72(s0)
  a4:	8e0a02fc 	lw	t2,764(s0)
  a8:	018d7021 	addu	t6,t4,t5
  ac:	01e2c021 	addu	t8,t7,v0
  b0:	03284821 	addu	t1,t9,t0
  b4:	314b0400 	andi	t3,t2,0x400
  b8:	ae0e001c 	sw	t6,28(s0)
  bc:	ae180020 	sw	t8,32(s0)
  c0:	ae090024 	sw	t1,36(s0)
  c4:	1160000e 	beqz	t3,100 <updateRacePlayerMode53AerialTrick+0x100>
  c8:	ae020074 	sw	v0,116(s0)
  cc:	8604007e 	lh	a0,126(s0)
  d0:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			d0: R_MIPS_26	fixedSine
  d4:	00000000 	nop
  d8:	00020823 	negu	at,v0
  dc:	00016080 	sll	t4,at,0x2
  e0:	01816023 	subu	t4,t4,at
  e4:	000c62c0 	sll	t4,t4,0xb
  e8:	05810003 	bgez	t4,f8 <updateRacePlayerMode53AerialTrick+0xf8>
  ec:	000c6b03 	sra	t5,t4,0xc
  f0:	25810fff 	addiu	at,t4,4095
  f4:	00016b03 	sra	t5,at,0xc
  f8:	1000000c 	b	12c <updateRacePlayerMode53AerialTrick+0x12c>
  fc:	a60d006e 	sh	t5,110(s0)
 100:	8604007e 	lh	a0,126(s0)
 104:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			104: R_MIPS_26	fixedSine
 108:	00000000 	nop
 10c:	00027080 	sll	t6,v0,0x2
 110:	01c27023 	subu	t6,t6,v0
 114:	000e72c0 	sll	t6,t6,0xb
 118:	05c10003 	bgez	t6,128 <updateRacePlayerMode53AerialTrick+0x128>
 11c:	000e7b03 	sra	t7,t6,0xc
 120:	25c10fff 	addiu	at,t6,4095
 124:	00017b03 	sra	t7,at,0xc
 128:	a60f006e 	sh	t7,110(s0)
 12c:	86080304 	lh	t0,772(s0)
 130:	8e18007c 	lw	t8,124(s0)
 134:	25090001 	addiu	t1,t0,1
 138:	a6090304 	sh	t1,772(s0)
 13c:	86020304 	lh	v0,772(s0)
 140:	24010008 	li	at,8
 144:	27190016 	addiu	t9,t8,22
 148:	14410006 	bne	v0,at,164 <updateRacePlayerMode53AerialTrick+0x164>
 14c:	ae19007c 	sw	t9,124(s0)
 150:	02002025 	move	a0,s0
 154:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			154: R_MIPS_26	setRaceMotionAnimation
 158:	2405001a 	li	a1,26
 15c:	86020304 	lh	v0,772(s0)
 160:	00000000 	nop
 164:	2401000f 	li	at,15
 168:	14410005 	bne	v0,at,180 <updateRacePlayerMode53AerialTrick+0x180>
 16c:	02002025 	move	a0,s0
 170:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			170: R_MIPS_26	setRaceMotionAnimation
 174:	2405001b 	li	a1,27
 178:	86020304 	lh	v0,772(s0)
 17c:	00000000 	nop
 180:	2401001e 	li	at,30
 184:	14410003 	bne	v0,at,194 <updateRacePlayerMode53AerialTrick+0x194>
 188:	02002025 	move	a0,s0
 18c:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			18c: R_MIPS_26	setRaceMotionAnimation
 190:	2405001c 	li	a1,28
 194:	8e02007c 	lw	v0,124(s0)
 198:	00000000 	nop
 19c:	28410401 	slti	at,v0,1025
 1a0:	14200003 	bnez	at,1b0 <updateRacePlayerMode53AerialTrick+0x1b0>
 1a4:	00000000 	nop
 1a8:	24020400 	li	v0,1024
 1ac:	ae02007c 	sw	v0,124(s0)
 1b0:	8e0b02fc 	lw	t3,764(s0)
 1b4:	284103d0 	slti	at,v0,976
 1b8:	356c0002 	ori	t4,t3,0x2
 1bc:	1020000f 	beqz	at,1fc <updateRacePlayerMode53AerialTrick+0x1fc>
 1c0:	ae0c02fc 	sw	t4,764(s0)
 1c4:	820f0014 	lb	t7,20(s0)
 1c8:	358e0800 	ori	t6,t4,0x800
 1cc:	15e0000b 	bnez	t7,1fc <updateRacePlayerMode53AerialTrick+0x1fc>
 1d0:	ae0e02fc 	sw	t6,764(s0)
 1d4:	3c180000 	lui	t8,0x0
			1d4: R_MIPS_HI16	gFrameCounter
 1d8:	87180000 	lh	t8,0(t8)
			1d8: R_MIPS_LO16	gFrameCounter
 1dc:	3c040000 	lui	a0,0x0
			1dc: R_MIPS_HI16	initRacePlayerLandingSnowSpray
 1e0:	33190001 	andi	t9,t8,0x1
 1e4:	13200005 	beqz	t9,1fc <updateRacePlayerMode53AerialTrick+0x1fc>
 1e8:	24840000 	addiu	a0,a0,0
			1e8: R_MIPS_LO16	initRacePlayerLandingSnowSpray
 1ec:	96070000 	lhu	a3,0(s0)
 1f0:	24050005 	li	a1,5
 1f4:	0c000000 	jal	0 <updateRacePlayerMode53AerialTrick>
			1f4: R_MIPS_26	createCallbackTaskWithUserIdPreservingArgs
 1f8:	24060002 	li	a2,2
 1fc:	8fbf001c 	lw	ra,28(sp)
 200:	8fb00018 	lw	s0,24(sp)
 204:	03e00008 	jr	ra
 208:	27bd0020 	addiu	sp,sp,32
 20c:	00000000 	nop
