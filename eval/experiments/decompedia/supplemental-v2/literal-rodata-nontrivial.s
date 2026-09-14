
/tmp/decompedia-supplemental-qwm2piby/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	27bdffd8 	addiu	sp,sp,-40
   4:	afb00020 	sw	s0,32(sp)
   8:	0004102a 	slt	v0,zero,a0
   c:	2490ffff 	addiu	s0,a0,-1
  10:	afbf0024 	sw	ra,36(sp)
  14:	e7b50018 	swc1	$f21,24(sp)
  18:	10400009 	beqz	v0,40 <probe+0x40>
  1c:	e7b4001c 	swc1	$f20,28(sp)
  20:	3c010000 	lui	at,0x0
			20: R_MIPS_HI16	.rodata
  24:	c4340000 	lwc1	$f20,0(at)
			24: R_MIPS_LO16	.rodata
  28:	00000000 	nop
  2c:	0c000000 	jal	0 <probe>
			2c: R_MIPS_26	sink
  30:	4600a306 	mov.s	$f12,$f20
  34:	0010102a 	slt	v0,zero,s0
  38:	1440fffc 	bnez	v0,2c <probe+0x2c>
  3c:	2610ffff 	addiu	s0,s0,-1
  40:	8fbf0024 	lw	ra,36(sp)
  44:	c7b50018 	lwc1	$f21,24(sp)
  48:	c7b4001c 	lwc1	$f20,28(sp)
  4c:	8fb00020 	lw	s0,32(sp)
  50:	03e00008 	jr	ra
  54:	27bd0028 	addiu	sp,sp,40
	...
