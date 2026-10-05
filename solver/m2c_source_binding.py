"""Explicit assembly snapshots for m2c's basename-only instruction metadata.

Source annotations are hypotheses until checked against assembled target bytes.
Duplicate basenames, synthetic instructions and missing annotations stay gaps.
No directory search or inferred filename resolution is performed.
"""
from __future__ import annotations

import hashlib
import copy
from pathlib import Path

from solver.m2c_uncertainty import Observer


class SourceBoundObserver(Observer):
    def __init__(self, *, source_files=(), **kwargs):
        super().__init__(**kwargs)
        self.sources, self.aliases = {}, {}
        for path in source_files:
            self.register_source(path)

    def register_source(self, path):
        path = Path(path).resolve()
        key = str(path)
        if key in self.sources:
            return
        if len(self.sources) >= 16:
            raise ValueError('assembly snapshot file cap exceeded')
        with path.open('rb') as stream:
            raw = stream.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('assembly snapshot byte cap exceeded')
        text = raw.decode('utf-8')
        self.sources[key] = {'text':text, 'sha256':hashlib.sha256(raw).hexdigest()}
        self.aliases[key] = key
        previous = self.aliases.get(path.name, key)
        self.aliases[path.name] = key if previous == key else None

    def _instruction(self, instruction):
        filename = instruction.meta.filename
        key = self.aliases.get(filename)
        # Supplying even None prevents the base observer from searching CWD
        # for an unrelated file that happens to have the same basename.
        self.files[filename] = self.sources[key]['text'] if key is not None else None
        row = super()._instruction(instruction)
        if key is None:
            row['byte_attribution_status'] = ('ambiguous-source-file' if filename in self.aliases
                                             else 'unregistered-source-file')
        else:
            row['resolved_source_path'] = key
            row['assembly_sha256'] = self.sources[key]['sha256']
            row['byte_attribution_status'] = ('synthetic-operation' if instruction.meta.synthetic
                                             else 'annotated-unverified' if 'word' in row
                                             else 'no-byte-annotation')
        return row

    def verified_report(self, source, target_object, *, function, function_address):
        """Verify available words; reject contradictory target annotations.

        This verifies a byte association, not source ownership or a C layout.
        The ordinary annotation report remains unchanged and retractable.
        """
        from solver.function_boundary import text_extent
        result = copy.deepcopy(super().report(source))
        raw = Path(target_object).read_bytes()
        section, size = text_extent(raw, function)
        address = int(function_address,0) if isinstance(function_address,str) else function_address
        checked, gaps = 0, 0
        for hazard in result['hazards']:
            instruction = hazard.get('instruction')
            if instruction is None or 'word' not in instruction:
                gaps += 1
                continue
            offset = int(instruction['address'],16) - address
            word = bytes.fromhex(instruction['word'])
            if offset < 0 or offset+4 > size or offset % 4 or len(word) != 4 or section[offset:offset+4] != word:
                raise ValueError('instruction annotation does not match target object bytes')
            instruction.update(byte_attribution_status='verified-target-word',
                byte_authority='checked target object section bytes; not original C', object_section_offset=offset)
            checked += 1
        result.update(target_object_sha256=hashlib.sha256(raw).hexdigest(), checked_target_words=checked,
                      unverified_instruction_associations=gaps,
                      binary_binding_scope='available non-synthetic annotated words only; not complete dataflow ownership')
        return result

