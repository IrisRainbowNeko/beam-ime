# Third-party notices

Beam's own code is Apache-2.0. `dependencies.lock.json` records source revisions.
Binary packages include this file and the texts under `licenses/`.

| Component | License | Source |
|---|---|---|
| llama.cpp / ggml | MIT | https://github.com/ggml-org/llama.cpp |
| librime | BSD-3-Clause | https://github.com/rime/librime |
| librime-lua | BSD-3-Clause | https://github.com/hchunhui/librime-lua |
| Lua | MIT | https://www.lua.org/ |
| rime-ice | GPL-3.0 | https://github.com/iDvel/rime-ice |
| Weasel | GPL-3.0 | https://github.com/rime/weasel/tree/0.17.4 |
| nlohmann/json | MIT | https://github.com/nlohmann/json |
| Boost | BSL-1.0 | https://www.boost.org/ |
| glog | BSD-3-Clause | https://github.com/google/glog |
| LevelDB | BSD-3-Clause | https://github.com/google/leveldb |
| marisa-trie | BSD-2-Clause / LGPL-2.1 | https://github.com/s-yata/marisa-trie |
| yaml-cpp | MIT | https://github.com/jbeder/yaml-cpp |
| OpenCC | Apache-2.0 | https://github.com/BYVoid/OpenCC |
| Vulkan headers / loader | Apache-2.0, MIT components | https://github.com/KhronosGroup |
| NSIS | zlib/libpng, component notices | https://nsis.sourceforge.io/License |
| SQLite | Public domain | https://sqlite.org/copyright.html |
| OpenSSL (Linux model fingerprinting) | Apache-2.0 | https://github.com/openssl/openssl |
| pypinyin pronunciation data | MIT | https://github.com/mozillazg/python-pinyin |

The Windows build statically links librime's dependencies from its pinned
submodules. Run `tools/collect_licenses.py` after fetching dependencies to collect
their supplied license files. The Windows glog configuration disables its
pre-initialization `__argv` fallback for Unicode Weasel hosts.

Rime Ice resources are redistributed at the pinned revision, with their license
and source link. Beam's schema adds its processor and translator to that scheme.
The offline Windows installer carries the original Weasel installer; the merged
`rime.dll` is built by the scripts in this repository. Weasel source is available
at the tag above, including its build instructions and submodule references.

The released model is a Qwen3-0.6B-Base fine-tune. Its model card and original
base-model attribution are in `docs/models/model-card.md`. Do not interpret the
license of Beam's source as a replacement for a dependency's license.

Optional native learning components use QVAC Fabric / llama.cpp (MIT), pinned at
2633d8d8cab160906601eeb9e3d29a4a12da88cc, with Beam's patch in `src/trainer/qvac.patch`.
Source: https://github.com/tetherto/qvac-fabric-llm.cpp . The component includes its
MIT license and the applicable dependency notices. It reuses the installed GGUF;
Python, PyTorch and CUDA runtimes are not distributed in learning assets. Personal
adapters and user training samples are generated locally and are not distributed.
