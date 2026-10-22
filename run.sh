#!/bin/bash
set -e
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PREFIX=$ROOT_DIR/target-root/usr/local
PYTHON_SITE=$PREFIX/lib/python3.12/site-packages
export PATH="$PREFIX/bin:$PYTHON_SITE/bin:$PATH"
export PYTHONPATH="$PYTHON_SITE${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$PREFIX/lib:$PYTHON_SITE/vapoursynth:$ROOT_DIR/NDI/sdk/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$ROOT_DIR/obecli" "$@"
