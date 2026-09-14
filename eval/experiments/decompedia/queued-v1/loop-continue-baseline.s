
/tmp/decompedia-queued-64h_ms83/probe.o:     file format elf32-tradbigmips


Disassembly of section .text:

00000000 <probe>:
   0:	00801825 	move	v1,a0
   4:	24040008 	li	a0,8
   8:	00001025 	move	v0,zero
   c:	00a03025 	move	a2,a1
  10:	8cce0000 	lw	t6,0(a2)
  14:	24420004 	addiu	v0,v0,4
  18:	25cf0001 	addiu	t7,t6,1
  1c:	ac6f0000 	sw	t7,0(v1)
  20:	8cd80004 	lw	t8,4(a2)
  24:	24630010 	addiu	v1,v1,16
  28:	27190001 	addiu	t9,t8,1
  2c:	ac79fff4 	sw	t9,-12(v1)
  30:	8cc80008 	lw	t0,8(a2)
  34:	24c60010 	addiu	a2,a2,16
  38:	25090001 	addiu	t1,t0,1
  3c:	ac69fff8 	sw	t1,-8(v1)
  40:	8ccafffc 	lw	t2,-4(a2)
  44:	00000000 	nop
  48:	254b0001 	addiu	t3,t2,1
  4c:	1444fff0 	bne	v0,a0,10 <probe+0x10>
  50:	ac6bfffc 	sw	t3,-4(v1)
  54:	03e00008 	jr	ra
  58:	00000000 	nop
  5c:	00000000 	nop
