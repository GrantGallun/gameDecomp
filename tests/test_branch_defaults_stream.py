"""The confirmed branch rewrite must reach the normal mutation stream."""
from solver import regalloc_mutations


def test_paired_defaults_are_reachable_without_a_custom_search_driver():
    source = '''int f(int choice) {
    int a;
    int b;
    a = 1;
    b = 2;
    if (choice == 0) {
        a = 3;
    } else {
        b = 4;
    }
    return a + b;
}
'''
    expected = '''int f(int choice) {
    int a;
    int b;
    if (choice == 0) {
        a = 3;
        b = 2;
    } else {
        a = 1;
        b = 4;
    }
    return a + b;
}
'''
    found = [(family, code) for _, family, code in regalloc_mutations.variants(source, 'f')]
    assert ('branch_defaults', expected) in found
