
/tmp/decompedia-queued-64h_ms83/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	27bdffe8 	addiu	sp,sp,-24
   4:	1080000a 	beqz	a0,30 <probe+0x30>
   8:	afbf0014 	sw	ra,20(sp)
   c:	24010001 	li	at,1
  10:	1081000b 	beq	a0,at,40 <probe+0x40>
  14:	24010002 	li	at,2
  18:	1081000d 	beq	a0,at,50 <probe+0x50>
  1c:	00000000 	nop
  20:	0c000000 	jal	0 <probe>
			20: R_MIPS_26	sink
  24:	24040009 	li	a0,9
  28:	1000000c 	b	5c <probe+0x5c>
  2c:	8fbf0014 	lw	ra,20(sp)
  30:	0c000000 	jal	0 <probe>
			30: R_MIPS_26	sink
  34:	24040002 	li	a0,2
  38:	10000008 	b	5c <probe+0x5c>
  3c:	8fbf0014 	lw	ra,20(sp)
  40:	0c000000 	jal	0 <probe>
			40: R_MIPS_26	sink
  44:	24040003 	li	a0,3
  48:	10000004 	b	5c <probe+0x5c>
  4c:	8fbf0014 	lw	ra,20(sp)
  50:	0c000000 	jal	0 <probe>
			50: R_MIPS_26	sink
  54:	24040004 	li	a0,4
  58:	8fbf0014 	lw	ra,20(sp)
  5c:	27bd0018 	addiu	sp,sp,24
  60:	03e00008 	jr	ra
  64:	00000000 	nop
	...
