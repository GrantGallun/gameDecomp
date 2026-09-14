
/tmp/decompedia-probe-ukydh9n7/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	3c020000 	lui	v0,0x0
			0: R_MIPS_HI16	g
   4:	24420000 	addiu	v0,v0,0
			4: R_MIPS_LO16	g
   8:	8c4e0000 	lw	t6,0(v0)
   c:	00000000 	nop
  10:	25cf0001 	addiu	t7,t6,1
  14:	03e00008 	jr	ra
  18:	ac4f0000 	sw	t7,0(v0)
  1c:	00000000 	nop
