"""Keep an identical candidate's artifact name within one verified worker job.

This is not a verifier/result cache. Every call still executes workspace.score,
including frontend checking, attribution, exact certification and lineage logging.
Only the existing pin-bound build cache may skip compiler work. Keeping its exact
command/path identity lets it recognize fresh duplicate candidates without
weakening its key or assuming filenames cannot affect generated code.
"""
from __future__ import annotations

import hashlib

from solver import workspace


class Names:
    def __init__(self, repo, ws):
        self.repo, self.ws = repo, ws
        self.pin = getattr(workspace, '_verified_build_cache_pin', None)
        self.names = {}

    def prior(self, source):
        # No cross-job or standalone result reuse. A runtime amendment also
        # invalidates this ledger; the build cache validates actual input bytes.
        if not self.pin or self.pin != getattr(workspace, '_verified_build_cache_pin', None):
            return None
        return self.names.get(source)

    def score(self, requested, source, **kwargs):
        name = self.prior(source) or requested
        if name != requested:
            kwargs['extra'] = {**(kwargs.get('extra') or {}), 'same_job_build_reuse': {
                'requested_artifact_name': requested, 'artifact_name': name,
                'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                'verified_pin_sha256': self.pin,
                'scope': 'same artifact address; ordinary score gates and cache input checks rerun'}}
        attempt = workspace.score(self.ws, self.repo, name, source, **kwargs)
        if attempt.compiled and self.pin == getattr(workspace, '_verified_build_cache_pin', None):
            self.names.setdefault(source, name)
        return name, attempt
