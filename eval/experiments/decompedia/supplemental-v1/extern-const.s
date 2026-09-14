
/tmp/decompedia-supplemental-odrijk49/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	27bdffe0 	addiu	sp,sp,-32
   4:	afb00014 	sw	s0,20(sp)
   8:	0004102a 	slt	v0,zero,a0
   c:	2490ffff 	addiu	s0,a0,-1
  10:	afbf001c 	sw	ra,28(sp)
  14:	10400009 	beqz	v0,3c <probe+0x3c>
  18:	afb10018 	sw	s1,24(sp)
  1c:	3c110000 	lui	s1,0x0
			1c: R_MIPS_HI16	k
  20:	26310000 	addiu	s1,s1,0
			20: R_MIPS_LO16	k
  24:	c62c0000 	lwc1	$f12,0(s1)
  28:	0c000000 	jal	0 <probe>
			28: R_MIPS_26	sink
  2c:	00000000 	nop
  30:	0010102a 	slt	v0,zero,s0
  34:	1440fffb 	bnez	v0,24 <probe+0x24>
  38:	2610ffff 	addiu	s0,s0,-1
  3c:	8fbf001c 	lw	ra,28(sp)
  40:	8fb00014 	lw	s0,20(sp)
  44:	8fb10018 	lw	s1,24(sp)
  48:	03e00008 	jr	ra
  4c:	27bd0020 	addiu	sp,sp,32
