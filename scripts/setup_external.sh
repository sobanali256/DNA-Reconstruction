#!/usr/bin/env bash
# Clone BBS and ITR at their pinned commits (scripts/external_pins.env) and build them.
#
#   bash scripts/setup_external.sh
#
# Result:
#   external/bbs/target/release/bbs                       BBS binary
#   external/reconstruction/Iterative/build/DNA           upstream ITR binary (unmodified)
#   external/itr_cli                                      our ITR wrapper (adapters/itr_native/)
#
# ITR's licence is "TBA", so its source lives only in external/ (git-ignored) and
# is never committed. Our wrapper links the upstream objects except DNA.o (upstream main).
# Safe to re-run: existing clones are reused and checked out at the pinned commit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=external_pins.env
source "$ROOT/scripts/external_pins.env"
EXT="$ROOT/external"
mkdir -p "$EXT"

clone_at() {  # clone_at <url> <dir> <commit>
    local url=$1 dir=$2 commit=$3
    if [ ! -d "$dir/.git" ]; then
        git clone --quiet "$url" "$dir"
    fi
    git -C "$dir" fetch --quiet origin
    git -C "$dir" checkout --quiet "$commit"
    local actual
    actual=$(git -C "$dir" rev-parse HEAD)
    if [ "$actual" != "$commit" ]; then
        echo "ERROR: $dir is at $actual, expected $commit" >&2
        exit 1
    fi
    echo "  $dir @ $commit"
}

echo "== Cloning upstream sources"
clone_at "$BBS_URL" "$EXT/bbs" "$BBS_COMMIT"
clone_at "$ITR_URL" "$EXT/reconstruction" "$ITR_COMMIT"

echo "== Building BBS (cargo, release)"
if [ -f "$HOME/.cargo/env" ]; then source "$HOME/.cargo/env"; fi
# Upstream compiler warnings go to a log; it is printed only if the build fails.
if ! (cd "$EXT/bbs" && cargo build --release --quiet 2> "$EXT/bbs_build.log"); then
    cat "$EXT/bbs_build.log" >&2
    exit 1
fi
echo "  $EXT/bbs/target/release/bbs"

echo "== Building upstream ITR (flags from the upstream README)"
ITR_SRC="$EXT/reconstruction/Iterative"
ITR_BUILD="$ITR_SRC/build"
mkdir -p "$ITR_BUILD"
CXXFLAGS="-std=c++0x -O3 -g3 -Wall -fmessage-length=0"
for f in "$ITR_SRC"/*.cpp; do
    name=$(basename "$f" .cpp)
    # Upstream DividerBMA.cpp produces many warnings; they are not ours to fix.
    if ! g++ $CXXFLAGS -c -o "$ITR_BUILD/$name.o" "$f" 2> "$ITR_BUILD/$name.log"; then
        cat "$ITR_BUILD/$name.log" >&2
        exit 1
    fi
done
g++ -o "$ITR_BUILD/DNA" "$ITR_BUILD"/*.o
echo "  $ITR_BUILD/DNA"

echo "== Building ITR wrapper (adapters/itr_native, same flags)"
WRAP_OBJS=$(ls "$ITR_BUILD"/*.o | grep -v '/DNA\.o$')
g++ $CXXFLAGS -I"$ITR_SRC" -o "$EXT/itr_cli" "$ROOT/adapters/itr_native/itr_cli.cpp" $WRAP_OBJS
echo "  $EXT/itr_cli"

echo "== Done"
