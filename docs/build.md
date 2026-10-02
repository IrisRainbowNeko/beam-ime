# 编译与打包

C++17、CMake 3.24+、Ninja。依赖版本见 `dependencies.lock.json`。
首次构建会下载固定版本的 llama.cpp，以及与已安装 librime 相同版本的 C++ 源码头文件。

## Arch

```sh
sudo pacman -S --needed base-devel cmake ninja git boost nlohmann-json librime fcitx5-rime glog vulkan-headers vulkan-icd-loader shaderc spirv-headers python python-yaml python-pypinyin
bash tools/build.sh release
python tools/package_linux.py --format arch --model models/beam-0.6b-q8_0.gguf
```

## Ubuntu 24.04

```sh
sudo apt install build-essential cmake ninja-build git pkg-config libboost-all-dev nlohmann-json3-dev librime-dev libgoogle-glog-dev libx11-dev libvulkan-dev glslc spirv-headers python3-yaml python3-pip
python3 -m pip install --break-system-packages pypinyin==0.55.0
bash tools/build.sh release
python3 tools/package_linux.py --format deb --model models/beam-0.6b-q8_0.gguf
```

省略 `--model` 只构建轻量程序包。CPU-only 构建：`bash tools/build.sh cpu`。
开发运行：`build/release/bin/beamd --model /path/model.gguf`。
不要把开发构建直接覆盖到正在运行的输入法。

可指定本地 checkout：

```sh
bash tools/build.sh release -DBEAM_LLAMA_SOURCE=/path/llama.cpp -DBEAM_RIME_SOURCE=/path/librime
```

本地 llama.cpp 应使用 lock 文件中的 revision；librime 必须匹配 `pkg-config --modversion rime`。

## Windows 交叉编译

正式 Windows 构建在 Linux 上使用 MinGW-w64，合并 librime、Lua 和 Beam 插件。
Arch 额外依赖：`mingw-w64-gcc wine 7zip`，以及上面的构建依赖。

```sh
bash packaging/windows/build.sh deps rime beamd
python3 tools/package_windows.py --model models/beam-0.6b-q8_0.gguf
```

输出在 `dist/`，含轻量版和离线版。NSIS 可使用系统 `makensis`，否则自动下载固定版本并通过 Wine 运行。
普通重新构建保留缓存；删除 `build/win/b` 可以重新配置，删除 `build/win/prefix` 可以完整重建依赖。
glog 每次重新配置，确保 `HAVE___ARGV=0` 被应用。

Windows 本机开发可在 MSYS2 MINGW64 shell 安装 gcc、cmake、ninja、nlohmann-json、Vulkan SDK 后，
直接使用根 CMake 构建 `beamd`。合并小狼毫和完整安装包使用上面的 Linux 交叉构建脚本。
首发不支持 MSVC。

## 测试

```sh
bash tools/build.sh tests
pwsh -NoProfile -File tests/windows-files.ps1
BEAM_TEST_MODEL=/path/model.gguf PYTHONPATH=tools:tools/models python3 -m unittest discover -s tests
```

最后一条增加真实 CPU 模型测试；设置 `BEAM_TEST_NGL=99` 可测 GPU。普通 CI 不下载模型。
