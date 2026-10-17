#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export a translate-only PEFT LoRA adapter as a GGUF adapter that beamd loads with --translate-adapter.

    PYTHONPATH=/path/llama.cpp/gguf-py python3 tools/models/export_translate_adapter.py \
        --llama-source /path/llama.cpp --base /path/ime-checkpoint --adapter /path/run/epoch-2/adapter \
        --output beam-translate.gguf

The GGUF records the prompt format (zh_en_v1 for mt_train.py adapters, which carry beam-mt.json; keys_llm_v4
otherwise) and decoding strategy, so the daemon needs no other flag.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import gguf


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--llama-source', type=Path, required=True)
    parser.add_argument('--base', type=Path, required=True, help='the full-precision checkpoint the base GGUF was made from')
    parser.add_argument('--adapter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--decoding-strategy', choices=('beam', 'greedy_first', 'field'), default='field')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        raw = Path(temporary) / 'adapter.gguf'
        subprocess.run([sys.executable, str(args.llama_source / 'convert_lora_to_gguf.py'), '--base', str(args.base),
                        '--outtype', 'f16', '--outfile', str(raw), str(args.adapter)], check=True)
        reader = gguf.GGUFReader(raw)
        arch = reader.fields[gguf.Keys.General.ARCHITECTURE].contents()
        writer = gguf.GGUFWriter(args.output, arch=arch, endianess=reader.endianess)
        for field in reader.fields.values():
            if field.name == gguf.Keys.General.ARCHITECTURE or field.name.startswith('GGUF.') or field.name.startswith('beam.'):
                continue
            val_type = field.types[0]
            sub_type = field.types[-1] if val_type == gguf.GGUFValueType.ARRAY else None
            writer.add_key_value(field.name, field.contents(), val_type, sub_type=sub_type)
        meta = args.adapter / 'beam-mt.json'
        version = json.loads(meta.read_text(encoding='utf-8'))['prompt_version'] if meta.exists() else 'keys_llm_v4'
        writer.add_string('beam.prompt_version', version)
        writer.add_string('beam.decoding_strategy', args.decoding_strategy)
        for t in reader.tensors:
            writer.add_tensor_info(t.name, t.data.shape, t.data.dtype, t.data.nbytes, t.tensor_type)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_ti_data_to_file()
        for t in reader.tensors:
            writer.write_tensor_data(t.data, tensor_endianess=reader.endianess)
        writer.close()
    print(f'wrote {args.output} ({args.output.stat().st_size >> 20} MiB)')


if __name__ == '__main__':
    main()
