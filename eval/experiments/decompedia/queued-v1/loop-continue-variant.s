
/tmp/decompedia-queued-64h_ms83/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	00001825 	move	v1,zero
   4:	00803025 	move	a2,a0
   8:	00a03825 	move	a3,a1
   c:	24020020 	li	v0,32
  10:	8cee0000 	lw	t6,0(a3)
  14:	24630010 	addiu	v1,v1,16
  18:	25cf0001 	addiu	t7,t6,1
  1c:	accf0000 	sw	t7,0(a2)
  20:	8cf80004 	lw	t8,4(a3)
  24:	24c60010 	addiu	a2,a2,16
  28:	27190001 	addiu	t9,t8,1
  2c:	acd9fff4 	sw	t9,-12(a2)
  30:	8ce80008 	lw	t0,8(a3)
  34:	24e70010 	addiu	a3,a3,16
  38:	25090001 	addiu	t1,t0,1
  3c:	acc9fff8 	sw	t1,-8(a2)
  40:	8ceafffc 	lw	t2,-4(a3)
  44:	00000000 	nop
  48:	254b0001 	addiu	t3,t2,1
  4c:	1462fff0 	bne	v1,v0,10 <probe+0x10>
  50:	accbfffc 	sw	t3,-4(a2)
  54:	03e00008 	jr	ra
  58:	00000000 	nop
  5c:	00000000 	nop
