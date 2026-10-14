#!/usr/bin/env bash
set -euo pipefail

if ! command -v clang &> /dev/null; then
    echo "[Buckthorn Phase 12] 'clang' is not installed in PATH. LLVM libFuzzer requires Clang with compiler-rt."
    echo "Please install clang (e.g., 'sudo apt install clang') to run native C coverage-guided fuzzing."
    exit 0
fi

echo "[Buckthorn Phase 12] Compiling standalone Clang libFuzzer + ASan + UBSan C binary..."
clang -O1 -g \
    -fsanitize=fuzzer,address,undefined \
    -fno-omit-frame-pointer \
    -Ibuckthorn -Ibuckthorn/c_src -Ibuckthorn/c_vendor \
    buckthorn/c_src/bt_fuzz_target.c \
    buckthorn/c_src/bt_http.c \
    buckthorn/c_src/bt_params.c \
    buckthorn/c_src/bt_json.c \
    buckthorn/c_src/bt_logger.c \
    buckthorn/c_src/bt_security.c \
    buckthorn/c_src/bt_middleware.c \
    buckthorn/c_src/bt_realtime.c \
    buckthorn/c_src/bt_audit.c \
    buckthorn/c_vendor/picohttpparser.c \
    buckthorn/c_vendor/rax.c \
    buckthorn/c_vendor/yyjson.c \
    -luv -lpthread -lcrypto \
    -o bt_fuzzer

echo "[Buckthorn Phase 12] Running libFuzzer for 10 seconds..."
./bt_fuzzer -max_total_time=10 -print_final_stats=1
rm -f bt_fuzzer
