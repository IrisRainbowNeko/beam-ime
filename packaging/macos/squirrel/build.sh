#!/bin/bash
# Builds Squirrel from source with a skin-image patch, then renders the candidate panel with
# a few test themes.
#
#   build.sh <squirrel tag or branch> <patch> <work dir>
#
# Leaves <work dir>/squirrel (the patched source tree and its build) and <work dir>/render/*.png.
set -euo pipefail

ref=$1
patch=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
work=$(mkdir -p "$3" && cd "$3" && pwd)
here=$(cd "$(dirname "$0")" && pwd)

git clone --depth 1 --branch "$ref" https://github.com/rime/squirrel.git "$work/squirrel"
cd "$work/squirrel"
git submodule update --init --depth 1 librime plum
git apply "$patch"
./action-install.sh
make release ARCHS=arm64

swiftc -O -enable-bare-slash-regex -target arm64-apple-macos13 -import-objc-header sources/Squirrel-Bridging-Header.h \
  -I librime/src -I librime/include -L lib -lrime.1 -F Frameworks -framework Sparkle \
  -Xlinker -rpath -Xlinker "$PWD/lib" -Xlinker -rpath -Xlinker "$PWD/Frameworks" \
  $(ls sources/*.swift | grep -v '/Main.swift$') "$here/render/main.swift" -o "$work/render-panel"

render() {  # <name> <candidate count> <style lines>
  local dir="$work/render/$1"
  mkdir -p "$dir"
  {
    echo 'patch:'
    echo '  style/color_scheme: skin_test'
    echo '  style/font_point: 16'
    printf '%s\n' "$3"
    echo '  preset_color_schemes/skin_test:'
    echo '    name: skin test'
    echo '    back_color: 0x00FFFFFF'
    echo '    border_color: 0x00FFFFFF'
    echo '    text_color: 0x333333'
    echo '    candidate_text_color: 0xDB2E61'
    echo '    hilited_candidate_back_color: 0x80FFFFFF'
    echo '    hilited_candidate_text_color: 0x8B0000'
    echo '    back_image: skin.png'
    echo '    back_image_slice: {left: 40, right: 60, top: 20, bottom: 30}'
    echo '    back_image_padding: {left: 30, right: 50, top: 16, bottom: 24}'
  } > "$dir/squirrel.custom.yaml"
  echo "== $1"
  "$work/render-panel" "$dir" "$PWD/data" "$work/render/$1.png" "$2"
}

render linear 7 '  style/candidate_list_layout: linear
  style/text_orientation: horizontal'
render stacked 5 '  style/candidate_list_layout: stacked
  style/text_orientation: horizontal'
render vertical 5 '  style/candidate_list_layout: stacked
  style/text_orientation: vertical'
render small 1 '  style/candidate_list_layout: linear
  style/text_orientation: horizontal'
