#!/usr/bin/env bash
# Build lambdas/<name>/deployment_package.zip with the shared common/ package
# bundled in (no Lambda layer - avoids the layer-ordering failures seen on
# buzzflow). Usage: scripts/build-lambda.sh <lambda-dir-name>
set -euo pipefail

NAME="${1:?usage: build-lambda.sh <lambda-name>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/lambdas/$NAME"
COMMON="$ROOT/layers/common/python/common"

[ -f "$SRC/lambda_function.py" ] || { echo "error: $SRC/lambda_function.py not found" >&2; exit 1; }

BUILD="$SRC/package"
rm -rf "$BUILD" "$SRC/deployment_package.zip"
mkdir -p "$BUILD"

# Shared deps (requests etc.) + per-function deps. boto3 is provided by the
# Lambda runtime; pinning it here only bloats the zip, so skip it.
grep -hv '^boto3' "$ROOT/layers/common/requirements.txt" "$SRC/requirements.txt" 2>/dev/null \
  | grep -v '^\s*#' | grep -v '^\s*$' > "$BUILD/.reqs.txt" || true
if [ -s "$BUILD/.reqs.txt" ]; then
  pip install --quiet --target "$BUILD" -r "$BUILD/.reqs.txt" --upgrade
fi
rm -f "$BUILD/.reqs.txt"

cp -r "$COMMON" "$BUILD/common"
cp "$SRC/lambda_function.py" "$BUILD/"
# Any extra modules next to the handler (e.g. table_configs.py)
find "$SRC" -maxdepth 1 -name '*.py' ! -name 'lambda_function.py' -exec cp {} "$BUILD/" \;

find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +
(cd "$BUILD" && zip -q -r9 ../deployment_package.zip .)
rm -rf "$BUILD"
echo "built lambdas/$NAME/deployment_package.zip ($(du -h "$SRC/deployment_package.zip" | cut -f1))"
