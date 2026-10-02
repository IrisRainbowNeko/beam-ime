# Sourced by build.sh: beamd.exe with optional Vulkan and a portable x86_64 CPU backend.
# Only the Vulkan loader's import library is needed; vulkan-1.dll comes with the GPU driver.
[ -d "$P/share/cmake/SPIRV-Headers" ] || cm "$S/SPIRV-Headers" spirv-headers -DSPIRV_HEADERS_ENABLE_TESTS=OFF
if [ ! -f "$P/lib/libvulkan-1.dll.a" ]; then
  cm "$S/Vulkan-Headers" vulkan-headers
  cm "$S/Vulkan-Loader" vulkan-loader -DVULKAN_HEADERS_INSTALL_DIR="$P" -DBUILD_TESTS=OFF \
    -DBUILD_SHARED_LIBS=ON -DUSE_GAS=ON -DCMAKE_CXX_FLAGS="" -DCMAKE_SHARED_LINKER_FLAGS="-static-libgcc"
fi
llama_src=${BEAM_LLAMA_SOURCE:-}
cmake -S "$root" -B "$W/b/ime" -G Ninja -DCMAKE_TOOLCHAIN_FILE="$T" -DBEAM_WIN_PREFIX="$P" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo ${llama_src:+-DBEAM_LLAMA_SOURCE=$llama_src} \
  -DBEAM_NATIVE=OFF -DBUILD_TESTING=OFF -DGGML_OPENMP=OFF \
  -DVulkan_INCLUDE_DIR="$P/include" -DVulkan_LIBRARY="$P/lib/libvulkan-1.dll.a" \
  -DVulkan_GLSLC_EXECUTABLE="$(command -v glslc)" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  > "$W/b-beamd.log" 2>&1
cmake --build "$W/b/ime" >> "$W/b-beamd.log" 2>&1
mkdir -p "$W/out"
cp "$W/b/ime/bin/beamd.exe" "$W/b/ime/bin/"*.dll "$W/out/"
mkdir -p "$W/symbols"
for binary in "$W/out/beamd.exe" "$W/out/"ggml*.dll "$W/out/llama.dll"; do
  ${TRIPLE}-objcopy --only-keep-debug "$binary" "$W/symbols/$(basename "$binary").debug"
  ${TRIPLE}-strip "$binary"
done
echo "built out/beamd.exe"
