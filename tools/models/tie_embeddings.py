#!/usr/bin/env python3
"""Drop a GGUF's output.weight when it is a byte-identical copy of token_embd.weight.

Qwen3-0.6B ties its input embedding and LM head, but our merged fine-tunes were exported with
both tensors (157 MB each at Q8_0). Without output.weight llama.cpp reuses token_embd for the
output layer, so results, speed and memory are unchanged; only the file shrinks.

    PYTHONPATH=/path/to/llama.cpp/gguf-py python3 ime/tools/tie_embeddings.py IN.gguf OUT.gguf
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

import gguf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()

    reader = gguf.GGUFReader(args.input)
    tensors = {t.name: t for t in reader.tensors}
    if "output.weight" not in tensors:
        sys.exit("no output.weight: already tied")
    out, embd = tensors["output.weight"], tensors["token_embd.weight"]
    if out.tensor_type != embd.tensor_type or not np.array_equal(np.asarray(out.data), np.asarray(embd.data)):
        sys.exit("output.weight differs from token_embd.weight: the model is really untied, keep both")

    arch = reader.fields[gguf.Keys.General.ARCHITECTURE].contents()
    writer = gguf.GGUFWriter(args.output, arch=arch, endianess=reader.endianess)
    for field in reader.fields.values():
        # GGUFWriter writes the architecture and the GGUF.* header fields itself.
        if field.name == gguf.Keys.General.ARCHITECTURE or field.name.startswith("GGUF."):
            continue
        val_type = field.types[0]
        sub_type = field.types[-1] if val_type == gguf.GGUFValueType.ARRAY else None
        writer.add_key_value(field.name, field.contents(), val_type, sub_type=sub_type)
    kept = [t for t in reader.tensors if t.name != "output.weight"]
    for t in kept:
        writer.add_tensor_info(t.name, t.data.shape, t.data.dtype, t.data.nbytes, t.tensor_type)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()
    for t in kept:
        writer.write_tensor_data(t.data, tensor_endianess=reader.endianess)
    writer.close()
    print(f"dropped output.weight ({out.n_bytes >> 20} MB)")


if __name__ == "__main__":
    main()
