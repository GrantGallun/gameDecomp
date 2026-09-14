extern int g; extern void sink(int);
void probe(void) { int x; x = g + 1; sink(x); }
