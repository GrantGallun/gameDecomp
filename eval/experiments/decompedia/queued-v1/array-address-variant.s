
/tmp/decompedia-queued-64h_ms83/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	27bdffd8 	addiu	sp,sp,-40
   4:	afb20020 	sw	s2,32(sp)
   8:	afb1001c 	sw	s1,28(sp)
   c:	afb00018 	sw	s0,24(sp)
  10:	00008025 	move	s0,zero
  14:	00808825 	move	s1,a0
  18:	2412000a 	li	s2,10
  1c:	afbf0024 	sw	ra,36(sp)
  20:	00107080 	sll	t6,s0,0x2
  24:	0c000000 	jal	0 <probe>
			24: R_MIPS_26	sink
  28:	01d12021 	addu	a0,t6,s1
  2c:	26100001 	addiu	s0,s0,1
  30:	1612fffc 	bne	s0,s2,24 <probe+0x24>
  34:	00107080 	sll	t6,s0,0x2
  38:	8fbf0024 	lw	ra,36(sp)
  3c:	8fb00018 	lw	s0,24(sp)
  40:	8fb1001c 	lw	s1,28(sp)
  44:	8fb20020 	lw	s2,32(sp)
  48:	03e00008 	jr	ra
  4c:	27bd0028 	addiu	sp,sp,40
