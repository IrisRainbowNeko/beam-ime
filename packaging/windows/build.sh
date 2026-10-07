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
  # Preserve librime's UTF-8 filenames at every LevelDB filesystem boundary.
  if grep -Fq '::CreateFileA(' "$D/leveldb/util/env_windows.cc"; then
    patch --batch -d "$D/leveldb" -p1 <<'PATCH_LEVELDB'
diff --git a/util/env_windows.cc b/util/env_windows.cc
index 449f564..5293ed2 100644
--- a/util/env_windows.cc
+++ b/util/env_windows.cc
@@ -19,0 +20 @@
+#include <filesystem>
@@ -378,2 +379,2 @@ class WindowsEnv : public Env {
-    ScopedHandle handle = ::CreateFileA(
-        filename.c_str(), desired_access, share_mode,
+    ScopedHandle handle = ::CreateFileW(
+        std::filesystem::u8path(filename).c_str(), desired_access, share_mode,
@@ -396 +397 @@ class WindowsEnv : public Env {
-        ::CreateFileA(filename.c_str(), desired_access, share_mode,
+        ::CreateFileW(std::filesystem::u8path(filename).c_str(), desired_access, share_mode,
@@ -441,2 +442,2 @@ class WindowsEnv : public Env {
-    ScopedHandle handle = ::CreateFileA(
-        filename.c_str(), desired_access, share_mode,
+    ScopedHandle handle = ::CreateFileW(
+        std::filesystem::u8path(filename).c_str(), desired_access, share_mode,
@@ -458,2 +459,2 @@ class WindowsEnv : public Env {
-    ScopedHandle handle = ::CreateFileA(
-        filename.c_str(), desired_access, share_mode,
+    ScopedHandle handle = ::CreateFileW(
+        std::filesystem::u8path(filename).c_str(), desired_access, share_mode,
@@ -472 +473 @@ class WindowsEnv : public Env {
-    return GetFileAttributesA(filename.c_str()) != INVALID_FILE_ATTRIBUTES;
+    return GetFileAttributesW(std::filesystem::u8path(filename).c_str()) != INVALID_FILE_ATTRIBUTES;
@@ -478,2 +479,2 @@ class WindowsEnv : public Env {
-    WIN32_FIND_DATAA find_data;
-    HANDLE dir_handle = ::FindFirstFileA(find_pattern.c_str(), &find_data);
+    WIN32_FIND_DATAW find_data;
+    HANDLE dir_handle = ::FindFirstFileW(std::filesystem::u8path(find_pattern).c_str(), &find_data);
@@ -488,8 +489,2 @@ class WindowsEnv : public Env {
-      char base_name[_MAX_FNAME];
-      char ext[_MAX_EXT];
-
-      if (!_splitpath_s(find_data.cFileName, nullptr, 0, nullptr, 0, base_name,
-                        ARRAYSIZE(base_name), ext, ARRAYSIZE(ext))) {
-        result->emplace_back(std::string(base_name) + ext);
-      }
-    } while (::FindNextFileA(dir_handle, &find_data));
+      result->emplace_back(std::filesystem::path(find_data.cFileName).u8string());
+    } while (::FindNextFileW(dir_handle, &find_data));
@@ -505 +500 @@ class WindowsEnv : public Env {
-    if (!::DeleteFileA(filename.c_str())) {
+    if (!::DeleteFileW(std::filesystem::u8path(filename).c_str())) {
@@ -512 +507 @@ class WindowsEnv : public Env {
-    if (!::CreateDirectoryA(dirname.c_str(), nullptr)) {
+    if (!::CreateDirectoryW(std::filesystem::u8path(dirname).c_str(), nullptr)) {
@@ -519 +514 @@ class WindowsEnv : public Env {
-    if (!::RemoveDirectoryA(dirname.c_str())) {
+    if (!::RemoveDirectoryW(std::filesystem::u8path(dirname).c_str())) {
@@ -527 +522 @@ class WindowsEnv : public Env {
-    if (!::GetFileAttributesExA(filename.c_str(), GetFileExInfoStandard,
+    if (!::GetFileAttributesExW(std::filesystem::u8path(filename).c_str(), GetFileExInfoStandard,
@@ -541 +536 @@ class WindowsEnv : public Env {
-    if (::MoveFileA(from.c_str(), to.c_str())) {
+    if (::MoveFileW(std::filesystem::u8path(from).c_str(), std::filesystem::u8path(to).c_str())) {
@@ -550 +545 @@ class WindowsEnv : public Env {
-    if (::ReplaceFileA(to.c_str(), from.c_str(), /*lpBackupFileName=*/nullptr,
+    if (::ReplaceFileW(std::filesystem::u8path(to).c_str(), std::filesystem::u8path(from).c_str(), /*lpBackupFileName=*/nullptr,
@@ -570,2 +565,2 @@ class WindowsEnv : public Env {
-    ScopedHandle handle = ::CreateFileA(
-        filename.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
+    ScopedHandle handle = ::CreateFileW(
+        std::filesystem::u8path(filename).c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
@@ -611,2 +606,2 @@ class WindowsEnv : public Env {
-    char tmp_path[MAX_PATH];
-    if (!GetTempPathA(ARRAYSIZE(tmp_path), tmp_path)) {
+    wchar_t tmp_path[MAX_PATH];
+    if (!GetTempPathW(ARRAYSIZE(tmp_path), tmp_path)) {
@@ -616 +611 @@ class WindowsEnv : public Env {
-    ss << tmp_path << "leveldbtest-" << std::this_thread::get_id();
+    ss << std::filesystem::path(tmp_path).u8string() << "leveldbtest-" << std::this_thread::get_id();
@@ -625 +620 @@ class WindowsEnv : public Env {
-    std::FILE* fp = std::fopen(filename.c_str(), "w");
+    std::FILE* fp = _wfopen(std::filesystem::u8path(filename).c_str(), L"w");
PATCH_LEVELDB
  fi
  # OpenCC's UTF-8 path support also applies to the MinGW runtime.
  if grep -Fq '#ifdef _MSC_VER' "$D/opencc/src/UTF8Util.hpp"; then
    patch --batch -d "$D/opencc" -p1 <<'PATCH_OPENCC'
diff --git a/src/Config.cpp b/src/Config.cpp
index a3a36b6..42b1c16 100644
--- a/src/Config.cpp
+++ b/src/Config.cpp
@@ -224 +224 @@ ConverterPtr Config::NewFromFile(const std::string& fileName) {
-  std::ifstream ifs(UTF8Util::GetPlatformString(prefixedFileName));
+  std::ifstream ifs(UTF8Util::GetPlatformString(prefixedFileName).c_str());
diff --git a/src/SerializableDict.hpp b/src/SerializableDict.hpp
index 17cea89..3ff85f8 100644
--- a/src/SerializableDict.hpp
+++ b/src/SerializableDict.hpp
@@ -51 +51 @@ public:
-#ifdef _MSC_VER
+#ifdef _WIN32
@@ -56 +56 @@ public:
-#endif // _MSC_VER
+#endif // _WIN32
diff --git a/src/UTF8Util.hpp b/src/UTF8Util.hpp
index 9d3e39b..95125d8 100644
--- a/src/UTF8Util.hpp
+++ b/src/UTF8Util.hpp
@@ -21 +21,2 @@
-#ifdef _MSC_VER
+#ifdef _WIN32
+#ifndef NOMINMAX
@@ -23,3 +24,3 @@
-#include <Windows.h>
-#undef NOMINMAX
-#endif // _MSC_VER
+#endif
+#include <windows.h>
+#endif // _WIN32
@@ -256 +257 @@ public:
-#ifdef _MSC_VER
+#ifdef _WIN32
@@ -262 +263 @@ public:
-#endif // _MSC_VER
+#endif // _WIN32
@@ -264 +265 @@ public:
-#ifdef _MSC_VER
+#ifdef _WIN32
@@ -288 +289 @@ public:
-#endif // _MSC_VER
+#endif // _WIN32
PATCH_OPENCC
  fi
  # (GCC 16 no longer pulls <cstdint> in transitively; old deps relied on it.)
  cm "$D/leveldb" leveldb -DCMAKE_CXX_STANDARD=17 -DLEVELDB_BUILD_BENCHMARKS=OFF -DLEVELDB_BUILD_TESTS=OFF
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
