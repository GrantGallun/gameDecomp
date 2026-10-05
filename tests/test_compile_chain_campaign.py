"""Campaign wiring of the compile chain: it runs only when compile recovery left nothing compiling."""
import inspect

from eval import agentrepair


def test_chain_hook_sits_after_compile_recovery_and_is_gated_on_nothing_compiling():
    source = inspect.getsource(agentrepair.run)
    recovery = source.index("compile_recovery.variants(")
    hook = source.index("_compile_chain(")
    frontier = source.index("seed_state=modelrepair._frontier([seed_state,*initial_states],1)[0]", recovery)
    assert recovery < hook < frontier
    assert "if not any(s.attempt.compiled for s in [seed_state,*initial_states]):" in source


def test_chain_hook_scores_through_the_logged_path_and_reports():
    source = inspect.getsource(agentrepair._compile_chain)
    assert "compiled_names.score(" in source and "strategy='agentrepair-compile-chain:'" in source
    assert "context_reports.append({'kind': 'compile-chain'" in source
    assert "compile_chain.chain(" in source
