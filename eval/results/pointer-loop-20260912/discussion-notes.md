# Lessons from supplied Discord excerpts

These are user-supplied discussion excerpts, not tested compiler rules. They mix
projects and architectures; compiler-specific behavior needs a reproducer with
the actual project recipe. No original target C was supplied or consulted.

* Switch-case reconstruction is a useful bounded model task. Existing CFG,
  jump-table and differential-path machinery provides more reliable inputs than
  asking for a replacement whole function with only a decompiler listing.
* Register names are not permanent source variable identities. Existing
  `dataflow` and `exactness_gradient` machinery exposes some def/use, register
  correspondence and lexical overwrite epochs. Compiler pre-allocation values
  are not fully recoverable from post-allocation register names.
* A large function affecting inline decisions is a compiler/recipe hypothesis,
  not a reason to change this project's flags or declarations without testing.
* Near-exact regressions motivate bounded joint source experiments. We can test
  combinations even when one component's score is worse, retain every receipt,
  and use full caller/object authority. No score threshold proves equivalence.
* Array traversal experiments must preserve byte/element units, iteration count,
  evaluation order and exit behavior. Merely trying different stride constants
  until the score rises is not a behavior-preserving strategy.

This experiment selects one current source-bound candidate, runs a private IDO
baseline and bounded pointer-loop alternatives, then checks current semantics
where supported. It does not modify canonical C or import campaign statuses.
