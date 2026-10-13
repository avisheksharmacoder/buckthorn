#!/usr/bin/env bash
set -euo pipefail

VENDOR_DIR="buckthorn/c_vendor"
mkdir -p "$VENDOR_DIR"

echo "[1/3] Fetching H2O picohttpparser (SIMD HTTP/1.1 parser)..."
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.c -o "$VENDOR_DIR/picohttpparser.c"
curl -sSL https://raw.githubusercontent.com/h2o/picohttpparser/master/picohttpparser.h -o "$VENDOR_DIR/picohttpparser.h"

echo "[2/3] Fetching Redis rax (ANSI C Radix Tree)..."
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.c -o "$VENDOR_DIR/rax.c"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax.h -o "$VENDOR_DIR/rax.h"
curl -sSL https://raw.githubusercontent.com/antirez/rax/master/rax_malloc.h -o "$VENDOR_DIR/rax_malloc.h"

echo "[3/3] Fetching ibireme/yyjson (SIMD C99 JSON Engine)..."
curl -sSL https://raw.githubusercontent.com/ibireme/yyjson/master/src/yyjson.c -o "$VENDOR_DIR/yyjson.c"
curl -sSL https://raw.githubusercontent.com/ibireme/yyjson/master/src/yyjson.h -o "$VENDOR_DIR/yyjson.h"

echo "Vendor C files ready in $VENDOR_DIR"
