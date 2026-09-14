
/tmp/decompedia-queued-64h_ms83/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	27bdffe8 	addiu	sp,sp,-24
   4:	10800008 	beqz	a0,28 <probe+0x28>
   8:	afbf0014 	sw	ra,20(sp)
   c:	24010001 	li	at,1
  10:	10810009 	beq	a0,at,38 <probe+0x38>
  14:	24010002 	li	at,2
  18:	1081000b 	beq	a0,at,48 <probe+0x48>
  1c:	00000000 	nop
  20:	1000000d 	b	58 <probe+0x58>
  24:	00000000 	nop
  28:	0c000000 	jal	0 <probe>
			28: R_MIPS_26	sink
  2c:	24040002 	li	a0,2
  30:	1000000c 	b	64 <probe+0x64>
  34:	8fbf0014 	lw	ra,20(sp)
  38:	0c000000 	jal	0 <probe>
			38: R_MIPS_26	sink
  3c:	24040003 	li	a0,3
  40:	10000008 	b	64 <probe+0x64>
  44:	8fbf0014 	lw	ra,20(sp)
  48:	0c000000 	jal	0 <probe>
			48: R_MIPS_26	sink
  4c:	24040004 	li	a0,4
  50:	10000004 	b	64 <probe+0x64>
  54:	8fbf0014 	lw	ra,20(sp)
  58:	0c000000 	jal	0 <probe>
			58: R_MIPS_26	sink
  5c:	24040009 	li	a0,9
  60:	8fbf0014 	lw	ra,20(sp)
  64:	27bd0018 	addiu	sp,sp,24
  68:	03e00008 	jr	ra
  6c:	00000000 	nop
