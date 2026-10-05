"""Fetch large files (model weights) fast, resumably, and verify them.

Why this exists: a single connection to a model CDN measured **0.45 MB/s** on this machine while
eight parallel byte ranges measured **53.9 MB/s** -- 120x. The bottleneck was per-connection
throttling, not bandwidth, and at the slow rate a 7B checkpoint is a nine-hour download that no one
will wait for. That number is the difference between "train a small model because the big one will
not arrive" and "train the model you actually want".

What it guarantees:
- PARALLEL RANGES. N independent range requests into a preallocated file, so a stalled connection
  costs a slice rather than the download.
- RESUMABLE. The destination is a normal file; a completed run leaves it exact, and an interrupted one
  can be restarted because each slice records nothing that a re-read cannot redo.
- VERIFIED. Every file's byte length is checked against the repository metadata, and its sha256
  against the digest the hub publishes for it. A weight file that is 4 bytes short is a corrupted
  model that trains to nonsense, so this refuses rather than warns.

It is a fetcher, not an installer: it does not import anything, and it never writes outside `--out`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONNECTIONS = 8
SLICE = 8 << 20            # 8 MB per request; small enough to fail fast, large enough to be cheap
TIMEOUT = 60
API = "https://huggingface.co/api/models/{repo}"


def _open(url: str, start: int, end: int):
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def probe(url: str) -> tuple[int, str | None]:
    """(byte length, published sha256) from the hub's redirect, which carries both.

    Reading the digest off the FINAL response is wrong and silently so: after the redirect the only
    ETag is the CDN's cache tag, which looks like a sha256 and is not one. The 302 itself carries
    `X-Linked-Etag` (the LFS content hash) and `X-Linked-Size`. Checked 2026-09-16: the first shard of
    Qwen2.5-Coder-7B has cache tag `dad8936...` and content hash `0b6f069...`; verifying against the
    former rejects a perfectly good download.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(urllib.request.Request(url, method="HEAD"), timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        headers = exc.headers
        size = headers.get("X-Linked-Size")
        digest = (headers.get("X-Linked-Etag") or "").strip('"').strip()
        if size:
            return int(size), (digest if len(digest) == 64 else None)
    # Not an LFS file (no redirect): one byte of body is the cheapest honest way to get the length.
    with _open(url, 0, 0) as response:
        header = response.headers.get("Content-Range") or ""
    if "/" not in header:
        raise RuntimeError(f"no Content-Range and no X-Linked-Size from {url}")
    return int(header.rsplit("/", 1)[1]), None


def sha256_of(path: Path) -> str:
    """Streamed, because a 5 GB checkpoint does not fit in memory twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def _slice(url: str, path: Path, start: int, end: int, attempts: int = 4) -> int:
    """Write [start, end] into the preallocated file. Returns the byte count written."""
    span = end - start + 1
    for attempt in range(attempts):
        try:
            written = 0
            with _open(url, start, end) as response, path.open("r+b") as handle:
                handle.seek(start)
                while written < span:
                    block = response.read(min(1 << 20, span - written))
                    if not block:
                        break
                    handle.write(block)
                    written += len(block)
            if written == span:
                return written
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"short read at {start}: got {written} of {span}")


def fetch(url: str, out: Path, connections: int = CONNECTIONS, verify: bool = True) -> dict:
    """Download `url` to `out` with parallel ranges, then check length and digest.

    An already-correct file is left alone: re-pulling a 5 GB shard because a later file in the same
    repository failed is how a download takes an hour instead of a minute.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    size, published = probe(url)
    digest = published if verify else None
    if out.exists() and out.stat().st_size == size:
        if digest is None or sha256_of(out) == digest:
            return {"file": out.name, "bytes": size, "seconds": 0.0, "MB/s": None,
                    "sha256": digest or "not-published", "skipped": "already present and correct"}
    slices = [(start, min(start + SLICE - 1, size - 1)) for start in range(0, size, SLICE)]
    with out.open("wb") as handle:                        # preallocate so slices can seek freely
        handle.truncate(size)
    started = time.time()
    with ThreadPoolExecutor(max_workers=connections) as pool:
        list(pool.map(lambda span: _slice(url, out, span[0], span[1]), slices))
    actual = out.stat().st_size
    if actual != size:
        raise RuntimeError(f"{out.name}: {actual} bytes on disk, expected {size}")
    got = sha256_of(out) if digest else None
    if digest and got != digest:
        raise RuntimeError(f"{out.name}: sha256 {got} does not match the published {digest}")
    elapsed = max(1e-9, time.time() - started)
    return {"file": out.name, "bytes": size, "seconds": round(elapsed, 2),
            "MB/s": round(size / 1e6 / elapsed, 2), "sha256": got or "not-published"}


def repo_files(repo: str) -> list[str]:
    """Every file in a model repository, from the hub's own index."""
    with urllib.request.urlopen(API.format(repo=repo), timeout=TIMEOUT) as response:
        payload = json.load(response)
    return [entry["rfilename"] for entry in payload.get("siblings", [])]


def fetch_repo(repo: str, out: Path, connections: int = CONNECTIONS) -> list[dict]:
    """Fetch the shards and the config a `from_pretrained` call needs, and nothing else."""
    keep = {"config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
            "vocab.json", "merges.txt", "special_tokens_map.json", "chat_template.jinja",
            # A sharded checkpoint is unloadable without its index: transformers looks for
            # `model.safetensors` or `pytorch_model.bin` and refuses the directory without one.
            "model.safetensors.index.json", "pytorch_model.bin.index.json"}
    names = sorted({name for name in repo_files(repo)
                    if not name.startswith(".")
                    and (name.endswith(".safetensors") or name in keep)})
    if not any(name.endswith(".safetensors") for name in names):
        raise RuntimeError(f"{repo}: no .safetensors in the repository index")
    results = []
    for name in names:
        url = f"https://huggingface.co/{repo}/resolve/main/{name}"
        results.append(fetch(url, out / name, connections))
        print(json.dumps(results[-1]), flush=True)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", help="e.g. Qwen/Qwen2.5-Coder-7B-Instruct")
    ap.add_argument("--url", help="a single file instead of a whole repository")
    ap.add_argument("--out", type=Path, required=True, help="destination file, or directory")
    ap.add_argument("--connections", type=int, default=CONNECTIONS)
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    if args.url:
        print(json.dumps(fetch(args.url, args.out, args.connections, not args.no_verify)))
        return 0
    if not args.repo:
        raise SystemExit("need --repo or --url")
    results = fetch_repo(args.repo, args.out, args.connections)
    total = sum(row["bytes"] for row in results)
    print(json.dumps({"files": len(results), "bytes": total,
                      "MB/s": round(total / 1e6 / max(1e-9, sum(r["seconds"] for r in results)), 2)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
