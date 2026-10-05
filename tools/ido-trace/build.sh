#!/usr/bin/env bash
# Build the tracing IDO 5.3 toolchain: stock ido-static-recomp v1.0 with ecvt/fcvt implemented.
#
#   tools/ido-trace/build.sh [WORK_DIR] [STOCK_TOOLCHAIN_DIR]
#
# Produces WORK_DIR/ido-trace: a copy of the stock toolchain with only `uopt` replaced.
# The production toolchain is never modified. Rebuilding embeds a new build date, so the
# binary hash changes: rerun the ROM gate in README.md before trusting a rebuild.
set -euo pipefail

WORK=${1:-$HOME/decomp/tools-src}
STOCK=${2:-$HOME/decomp/sbk1/tools/ido-recomp/linux}
HERE=$(cd "$(dirname "$0")" && pwd)
COMMIT=461f6b5f14606655bc0abd1c92f2508f60b1417c   # decompals/ido-static-recomp tag v1.0

mkdir -p "$WORK"
cd "$WORK"
[ -d ido-static-recomp ] || git clone https://github.com/decompals/ido-static-recomp
cd ido-static-recomp
git checkout -q "$COMMIT"
git checkout -q -- libc_impl.c
git apply "$HERE/ecvt-fcvt.patch"
git submodule update --init -q
make setup -j"$(nproc)"
make VERSION=5.3 RELEASE=1 -j"$(nproc)" build/5.3/out/uopt

rm -rf "$WORK/ido-trace"
cp -a "$STOCK" "$WORK/ido-trace"
cp build/5.3/out/uopt "$WORK/ido-trace/uopt"
sha256sum "$WORK/ido-trace/uopt"
