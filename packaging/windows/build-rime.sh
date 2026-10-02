# Sourced by build.sh: librime + librime-lua + Beam merged into one 64-bit rime.dll.
ln -sfn /usr/include/nlohmann "$P/include/nlohmann"
mkdir -p "$S/librime/plugins"
ln -sfn "$S/librime-lua" "$S/librime/plugins/lua"
if [ -d "$S/librime/plugins/beam" ] && [ ! -L "$S/librime/plugins/beam" ]; then
  mv "$S/librime/plugins/beam" "$S/librime/plugins/beam.previous.$(date +%s)"
fi
ln -sfn "$root/src/rime" "$S/librime/plugins/beam"
# The plugin list is fixed so a stray directory under plugins/ cannot sneak in.
RIME_PLUGINS="lua beam" cmake -S "$S/librime" -B "$W/b/librime" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$T" \
    -DBEAM_WIN_PREFIX="$P" -DBEAM_SOURCE_ROOT="$root" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$W/b/librime-install" \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint" \
  -DBUILD_STATIC=ON -DBUILD_SHARED_LIBS=ON -DBUILD_MERGED_PLUGINS=ON -DBUILD_TEST=OFF \
  -DENABLE_LOGGING=ON -DENABLE_EXTERNAL_PLUGINS=OFF -DBoost_INCLUDE_DIR="$P/include" \
  > "$W/b-librime.log" 2>&1
cmake --build "$W/b/librime" >> "$W/b-librime.log" 2>&1
mkdir -p "$W/out"
cp "$W/b/librime/lib/rime.dll" "$W/out/rime.dll" 2>/dev/null || cp "$W"/b/librime/bin/*rime*.dll "$W/out/rime.dll"
mkdir -p "$W/symbols"
${TRIPLE}-objcopy --only-keep-debug "$W/out/rime.dll" "$W/symbols/rime.dll.debug"
${TRIPLE}-strip "$W/out/rime.dll"
${TRIPLE}-g++ -std=c++17 -municode -mwindows -static -I"$S/librime/src" \
  "$root/tests/windows-rime-host.cc" -o "$W/out/beam-rime-host-test.exe"
echo "built out/rime.dll"
