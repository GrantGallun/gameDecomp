extern int g; extern void sink(int);
void probe(void) { sink(g + 1); }
