#!/usr/bin/env bash
# Build the macOS (Squirrel) package of Beam IME on an Apple Silicon Mac.
#
#   packaging/macos/build.sh [deps] [build] [squirrel] [package]
#   BEAM_MODEL=beam-0.6b-q8_0.gguf BEAM_ADAPTER=beam-translate-en-v1.gguf packaging/macos/build.sh package
#
# Outputs under build/macos:
#   squirrel/            the pinned official Squirrel package, expanded; its librime.1.dylib is what
#                        the plugin links against and what users have installed
#   build/               beamd (llama.cpp, Metal + CPU), librime-beam.dylib, tests
#   squirrel-src/        Squirrel built from the same pinned source with Beam's patch for skin
#                        images (back_image); bin/Squirrel replaces the official app's executable
#   ../../dist/Beam-<ver>-macos-arm64[-offline].pkg
# Requirements: Xcode (the squirrel stage), cmake, ninja, git, python3 with pypinyin and PyYAML.
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
M=$root/build/macos
D=$M/downloads
stages=${*:-deps build squirrel package}
mkdir -p "$D"
export CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL:-$(sysctl -n hw.ncpu)}

lock() {  # lock KEY... : a value from dependencies.lock.json
    python3 -c 'import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]: value = value[key]
print(value)' "$root/dependencies.lock.json" "$@"
}
SQUIRREL_VERSION=$(lock squirrel version)
SQUIRREL_PKG=$D/Squirrel-$SQUIRREL_VERSION.pkg
SQUIRREL_APP=$M/squirrel/Payload/Squirrel.app
RIME_VERSION=$(lock macos-librime version)
RIME_SOURCE=$M/librime
BOOST_VERSION=$(lock boost-headers-macos version)
BOOST=$M/boost_${BOOST_VERSION//./_}

fetch() {  # fetch URL FILE SHA256
    if [ ! -f "$2" ]; then curl -fL --retry 3 -o "$2.part" "$1"; mv "$2.part" "$2"; fi
    echo "$3  $2" | shasum -a 256 -c - > /dev/null || { rm -f "$2"; echo "checksum mismatch: $2" >&2; exit 1; }
}

deps() {
    fetch "$(lock squirrel url)" "$SQUIRREL_PKG" "$(lock squirrel sha256)"
    if [ ! -d "$SQUIRREL_APP" ]; then
        rm -rf "$M/squirrel"
        pkgutil --expand-full "$SQUIRREL_PKG" "$M/squirrel"
    fi
    echo "$(lock squirrel librimeSha256)  $SQUIRREL_APP/Contents/Frameworks/librime.1.dylib" | shasum -a 256 -c - > /dev/null
    local revision; revision=$(lock macos-librime revision)
    if [ "$(git -C "$RIME_SOURCE" rev-parse HEAD 2>/dev/null)" != "$revision" ]; then
        rm -rf "$RIME_SOURCE"; git init -q "$RIME_SOURCE"
        git -C "$RIME_SOURCE" fetch -q --depth 1 "$(lock macos-librime source)" "$revision"
        git -C "$RIME_SOURCE" checkout -q --detach FETCH_HEAD
    fi
    # Only the headers: librime's public C++ headers include Boost (signals2, ...).
    if [ ! -d "$BOOST/boost" ]; then
        fetch "$(lock boost-headers-macos url)" "$D/boost_${BOOST_VERSION//./_}.tar.gz" "$(lock boost-headers-macos sha256)"
        tar -xzf "$D/boost_${BOOST_VERSION//./_}.tar.gz" -C "$M" "boost_${BOOST_VERSION//./_}/boost"
    fi
    echo "deps ready: Squirrel $SQUIRREL_VERSION, librime $RIME_VERSION, Boost $BOOST_VERSION"
}

build() {
    cmake -S "$root" -B "$M/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 \
        ${BEAM_LLAMA_SOURCE:+-DBEAM_LLAMA_SOURCE=$BEAM_LLAMA_SOURCE} \
        -DBEAM_RIME_LIBRARY="$SQUIRREL_APP/Contents/Frameworks/librime.1.dylib" \
        -DBEAM_RIME_SOURCE="$RIME_SOURCE" -DRIME_VERSION="$RIME_VERSION" -DBEAM_BOOST_INCLUDE="$BOOST" \
        -DBEAM_RIME_PLUGIN_DIR:STRING=lib/beam-ime/rime-plugins  # relative to the prefix; a PATH value would be made absolute
    cmake --build "$M/build"
    cmake --build "$M/build" --target rime_smoke
    ctest --test-dir "$M/build" --output-on-failure
    # The real plugin inside Squirrel's librime, against a fixture daemon (no model needed).
    BEAM_TEST_RIME_BUILD="$M/build" BEAM_TEST_RIME_FRAMEWORKS="$SQUIRREL_APP/Contents/Frameworks" \
        python3 -m unittest discover -s "$root/tests" -p test_rime_translation.py -v
}

squirrel() {
    bash "$root/packaging/macos/squirrel/build.sh" "$SQUIRREL_VERSION" "$root/packaging/macos/squirrel/back-image.patch" \
        "$M/squirrel-src"
    local revision; revision=$(git -C "$M/squirrel-src/squirrel" rev-parse HEAD)
    [ "$revision" = "$(lock squirrel revision)" ] || { echo "Squirrel $SQUIRREL_VERSION is $revision, not the pinned revision" >&2; exit 1; }
    # The patched executable must load the official app's own frameworks (librime, Sparkle) just as the vendor build does.
    bundled() { otool -arch arm64 -L "$1" | tail -n +2 | awk '{print $1}' | grep -v -e '^/usr/lib/' -e '^/System/' | sort; }
    diff <(bundled "$SQUIRREL_APP/Contents/MacOS/Squirrel") <(bundled "$M/squirrel-src/bin/Squirrel")
}

package() {
    python3 "$root/tools/package_macos.py" --build "$M/build" --squirrel-pkg "$SQUIRREL_PKG" \
        --squirrel-executable "$M/squirrel-src/bin/Squirrel" \
        ${BEAM_MODEL:+--model "$BEAM_MODEL"} ${BEAM_ADAPTER:+--adapter "$BEAM_ADAPTER"}
}

for stage in $stages; do
    case $stage in
        deps|build|squirrel|package) "$stage" ;;
        *) echo "unknown stage $stage" >&2; exit 2 ;;
    esac
done
