#!/usr/bin/env python3
"""Pack white-eye alpha coverage into GBZ1 indices; share duplicate sequences.

The upstream encoder maps white RGBA on transparent to coverage levels 0..15.
Firmware supplies white ARGB palette entries with alpha=index*17. The binary
container and XOR/zlib decoder are unchanged; the original atlas is untouched.
"""
from pathlib import Path
import hashlib
import importlib.util
import struct
import sys
import tempfile


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: encode_atlas.py FRAME_DIR OUTPUT_ATLAS')
    source, output = map(Path, sys.argv[1:])
    original = Path(__file__).resolve().parents[2] / 'firmware/main/assets/assets_bin/grokbot.atlas'
    if output.resolve() == original:
        raise SystemExit('Refusing to overwrite the original Grok Bot atlas')
    spec = importlib.util.spec_from_file_location('upstream_encoder', Path(__file__).parent.parent / 'grokbot/encode_atlas.py')
    encoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(encoder)
    with tempfile.TemporaryDirectory() as temp:
        packed = Path(temp) / 'packed.atlas'
        args = sys.argv
        try:
            sys.argv = [args[0], str(source), str(packed)]
            encoder.main()
        finally:
            sys.argv = args
        raw = packed.read_bytes()
    count = raw[11]
    offset = 16 + count * 32
    seen, headers, chunks = {}, [], []
    for i in range(count):
        name, frames, start, pos, size, frame_size = struct.unpack_from('<16sHHIII', raw, 16 + i * 32)
        block = raw[pos:pos+size]
        # Use the actual immutable bytes as the key: no hash collision ambiguity.
        if block not in seen:
            seen[block] = offset
            offset += size
            chunks.append(block)
        headers.append(struct.pack('<16sHHIII', name, frames, start, seen[block], size, frame_size))
    result = raw[:16] + b''.join(headers) + b''.join(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    print(f'{count} states, {len(seen)} unique sequences; {len(raw)} -> {len(result)} bytes')
    print('sha256', hashlib.sha256(result).hexdigest())


if __name__ == '__main__':
    main()
