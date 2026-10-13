#!/usr/bin/env bash
set -euo pipefail

VENDOR_DIR="buckthorn/c_vendor"
mkdir -p "$VENDOR_DIR"

echo "[1/2] Fetching H2O picohttpparser (SIMD HTTP/1.1 parser)..."
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.c -o "$VENDOR_DIR/picohttpparser.c"
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.h -o "$VENDOR_DIR/picohttpparser.h"

echo "[2/2] Fetching Redis rax (ANSI C Radix Tree)..."
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.c -o "$VENDOR_DIR/rax.c"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.h -o "$VENDOR_DIR/rax.h"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax_malloc.h -o "$VENDOR_DIR/rax_malloc.h"

echo "Vendor C files ready in $VENDOR_DIR"
