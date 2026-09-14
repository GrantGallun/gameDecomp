
/tmp/decompedia-probe-ukydh9n7/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	3c040000 	lui	a0,0x0
			0: R_MIPS_HI16	g
   4:	8c840000 	lw	a0,0(a0)
			4: R_MIPS_LO16	g
   8:	27bdffe8 	addiu	sp,sp,-24
   c:	afbf0014 	sw	ra,20(sp)
  10:	0c000000 	jal	0 <probe>
			10: R_MIPS_26	sink
  14:	24840001 	addiu	a0,a0,1
  18:	8fbf0014 	lw	ra,20(sp)
  1c:	27bd0018 	addiu	sp,sp,24
  20:	03e00008 	jr	ra
  24:	00000000 	nop
	...
