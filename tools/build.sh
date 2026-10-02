#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
preset=${1:-release}
cd "$root"
cmake --preset "$preset" "${@:2}"
cmake --build --preset "$preset" --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"
ctest --test-dir "$root/build/$preset" --output-on-failure
