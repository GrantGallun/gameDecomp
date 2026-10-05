"""Preserve an attempt receipt when compilation raises before normal logging."""
from solver import workspace


def score_logged(ws, repo, name, source, *, conn, **metadata):
    try:
        return workspace.score(ws, repo, name, source, conn=conn, func=name, **metadata)
    except Exception as exc:
        attempt = workspace.Attempt(False, 0, False, "", f"{type(exc).__name__}: {exc}", "")
        workspace.record_attempt(conn, name, source, attempt, **metadata)
        return attempt
