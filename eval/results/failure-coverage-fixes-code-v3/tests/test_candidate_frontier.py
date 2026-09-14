from solver.candidate_frontier import Candidate, Frontier


def candidate(source, score, shape):
    return Candidate(source, None, None, "", source, (score,), shape)


def test_frontier_preserves_shapes_and_never_reexpands_source():
    frontier = Frontier(2)
    frontier.offer(candidate("best", 99, "same"))
    frontier.offer(candidate("near-duplicate", 98, "same"))
    frontier.offer(candidate("different", 90, "different"))
    assert frontier.pop().source == "best"
    assert frontier.pop().source == "different"
    assert not frontier.offer(candidate("best", 100, "new"))
    assert frontier.pop() is None
