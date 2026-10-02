#!/usr/bin/env bash
# Cross-build the Windows (Weasel) package of Beam IME on Linux with MinGW-w64.
#
#   packaging/windows/build.sh [deps] [rime] [beamd] [package]
#
# Outputs under build/win:
#   out/rime.dll         librime 1.13.1 (the version Weasel 0.17.4 ships) with librime-lua and the
#                        Beam plugin merged in; replaces Weasel's rime.dll (64-bit)
#   out/beamd.exe        engine service (llama.cpp, Vulkan + CPU)
#   BeamIME-<ver>-setup.exe   NSIS installer
# Requirements: mingw-w64-gcc, cmake, ninja, git, glslc, boost headers, curl, 7z, wine (for NSIS).
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
W=$root/build/win
P=$W/prefix
S=$W/src
T=$root/packaging/windows/mingw-w64-x86_64.cmake
TRIPLE=x86_64-w64-mingw32
LIBRIME_REV=1c23358157934bd6e6d6981f0c0164f05393b497
LUA_REV=6f30968058a3ca83c47949308ef0ddc51a11a264
stages=${*:-deps rime beamd package}
mkdir -p "$W" "$P/include" "$S"
ln -sfn "${BEAM_BOOST_INCLUDE:-/usr/include/boost}" "$P/include/boost"
export CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL:-4}

cm() {  # cm SRC BUILD [cmake args...]: configure + build + install a static dependency
  local src=$1 bld=$2; shift 2
  cmake -S "$src" -B "$W/b/$bld" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$T" -DBEAM_WIN_PREFIX="$P" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$P" -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint" "$@" > "$W/b-$bld.log" 2>&1
  cmake --build "$W/b/$bld" --target install >> "$W/b-$bld.log" 2>&1
  echo "built $bld"
}

fetch_at() {
  local directory=$1 url=$2 revision=$3
  if [ ! -d "$directory/.git" ]; then
    git init -q "$directory"
    git -C "$directory" remote add origin "$url"
  fi
  if [ "$(git -C "$directory" rev-parse HEAD 2>/dev/null)" != "$revision" ]; then
    git -C "$directory" fetch -q --depth 1 origin "$revision"
    git -C "$directory" checkout -q --detach "$revision"
  fi
}

fetch() {
  fetch_at "$S/librime" https://github.com/rime/librime.git "$LIBRIME_REV"
  git -C "$S/librime" submodule update --init --recursive --depth 1
  fetch_at "$S/librime-lua" https://github.com/hchunhui/librime-lua.git "$LUA_REV"
  fetch_at "$S/librime-lua/thirdparty" https://github.com/hchunhui/librime-lua.git 9e5bb71db1544913f8005dadc3df8439c00d08b7
  fetch_at "$S/Vulkan-Headers" https://github.com/KhronosGroup/Vulkan-Headers.git 2cd90f9d20df57eac214c148f3aed885372ddcfe
  fetch_at "$S/SPIRV-Headers" https://github.com/KhronosGroup/SPIRV-Headers.git 2a611a970fdbc41ac2e3e328802aed9985352dca
  fetch_at "$S/Vulkan-Loader" https://github.com/KhronosGroup/Vulkan-Loader.git da8d2caad9341ca8c5a7c3deba217d7da50a7c24
}

build_deps() {
  local D=$S/librime/deps
  # (GCC 16 no longer pulls <cstdint> in transitively; old deps relied on it.)
  cm "$D/leveldb" leveldb -DLEVELDB_BUILD_BENCHMARKS=OFF -DLEVELDB_BUILD_TESTS=OFF
  cm "$D/marisa-trie" marisa -DBUILD_TESTING=OFF -DENABLE_TOOLS=OFF
  cm "$D/yaml-cpp" yaml-cpp -DYAML_CPP_BUILD_CONTRIB=OFF -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF
  # HAVE___ARGV=0: before InitGoogleLogging, glog names the program from __argv[0], and librime's
  # SetupLogging creates the log files (SetLogSymlink) before initializing. In a wWinMain program
  # such as WeaselServer __argv is NULL, so that read crashed rime.dll; glog then says "UNKNOWN".
  cm "$D/glog" glog -DBUILD_TESTING=OFF -DWITH_GFLAGS=OFF -DWITH_UNWIND=OFF -DHAVE___ARGV=0
  # OpenCC: only the library (its data step would run the cross-built opencc_dict.exe; Weasel
  # ships its own OpenCC data). It uses the marisa built above; its bundled rapidjson trips a
  # GCC 16 template check.
    cmake -S "$D/opencc" -B "$W/b/opencc" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$T" -DBEAM_WIN_PREFIX="$P" \
      -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      -DBUILD_DOCUMENTATION=OFF -DENABLE_GTEST=OFF -DUSE_SYSTEM_MARISA=ON \
      -DCMAKE_CXX_FLAGS="-include cstdint -I$P/include -Wno-template-body" > "$W/b-opencc.log" 2>&1
    cmake --build "$W/b/opencc" --target libopencc >> "$W/b-opencc.log" 2>&1
    mkdir -p "$P/include/opencc"
    cp "$D"/opencc/src/*.hpp "$D"/opencc/src/*.h "$W"/b/opencc/src/*.h "$P/include/opencc/"
    cp "$W/b/opencc/src/libopencc.a" "$P/lib/"
    echo "built opencc"
}

case " $stages " in *" deps "*|*" rime "*|*" beamd "*) fetch ;; esac
for stage in $stages; do
  case $stage in
    deps) build_deps ;;
    rime|beamd) source "$root/packaging/windows/build-$stage.sh" ;;
    package) python3 "$root/tools/package_windows.py" ;;
    *) echo "unknown stage $stage" >&2; exit 2 ;;
  esac
done
