"""Generate conditional potential from a retained capability assessment.

This reuses supplied evidence. It does not refresh workspaces, execute repairs,
invoke compilers or claim that older observations describe today's source tree.
"""
import argparse
import json
from pathlib import Path

from solver.capability_operations import from_assessment


def export(assessment_path, output_path, *, node='root', max_depth=3, max_nodes=256):
    payload = json.loads(Path(assessment_path).read_text(encoding='utf-8'))
    assessment = payload['assessments'][node] if 'assessments' in payload else payload
    result = from_assessment(assessment, max_depth=max_depth, max_nodes=max_nodes)
    # Exclusive creation preserves both earlier experiments and accidental reruns.
    with Path(output_path).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assessment', required=True, type=Path)
    parser.add_argument('--node', default='root')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-depth', default=3, type=int)
    parser.add_argument('--max-nodes', default=256, type=int)
    args = parser.parse_args()
    result = export(args.assessment, args.output, node=args.node,
                    max_depth=args.max_depth, max_nodes=args.max_nodes)
    print(json.dumps({'nodes': len(result['nodes']), 'paths': len(result['paths']),
                      'goals': result['goals'], 'limits': result['limits'],
                      'new_compiler_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
