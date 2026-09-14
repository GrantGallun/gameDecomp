"""Capture an already stopped emulator/debugger, or replay a saved capture."""
import argparse
import json
from pathlib import Path
import socket
from eval.agentrepair import _atomic_json
from solver import runtime_capture as runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['capture', 'replay'])
    parser.add_argument('--rom', required=True, type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output already exists; preserve prior capture receipts')
    if args.action == 'capture':
        if not args.plan or not args.port:
            parser.error('capture requires --plan and --port')
        with socket.create_connection((args.host, args.port), timeout=15) as connection:
            result = runtime.capture(runtime.Remote(connection), json.loads(args.plan.read_text()), args.rom)
    else:
        if not all((args.capture, args.target, args.candidate, args.repo)):
            parser.error('replay requires --capture, --target, --candidate and --repo')
        result = runtime.replay(json.loads(args.capture.read_text()), args.rom,
                                args.target.read_text(), args.candidate.read_text(), repo=args.repo)
    _atomic_json(args.out, result)


if __name__ == '__main__':
    main()
