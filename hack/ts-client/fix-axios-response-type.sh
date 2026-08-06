#!/usr/bin/env bash
set -euo pipefail

# Gives createRequestFunction's returned closure an explicit Promise<R> return type.
#
# Why this is needed:
#   axios 1.19.0 declares `axiosResponseDefault: unique symbol` WITHOUT exporting it,
#   and routes request() through `AxiosResponseResult<T, R, D, P>` -- a conditional
#   type gated on that symbol. The generated common.ts lets TypeScript infer the
#   closure's return type, so declaration emit tries to name the inaccessible symbol
#   and fails with:
#     TS2527: The inferred type of 'createRequestFunction' references an inaccessible
#             'unique symbol' type. A type annotation is necessary.
#
# Why the annotation and cast are sound:
#   R is always supplied explicitly (it defaults to AxiosResponse<T>), so it is never
#   axios' internal default symbol. `R extends AxiosResponseDefault ? ... : R`
#   therefore always resolves to R. TypeScript cannot reduce the conditional while R
#   is still generic, hence the narrowing cast. The emitted signature is
#   `=> Promise<R>`, identical to axios <= 1.18 behaviour.
#
# Remove this once axios exports the symbol (or stops routing request() through the
# conditional), or once openapi-generator emits the annotation itself.
#
# Usage: fix-axios-response-type.sh <src-dir>

if [ $# -lt 1 ]; then
  echo "Usage: $0 <src-dir>" >&2
  exit 1
fi

SRC_DIR="$1"
COMMON="$SRC_DIR/common.ts"

if [ ! -f "$COMMON" ]; then
  echo "ERROR: $COMMON not found" >&2
  exit 1
fi

# Both substitutions are no-ops if already applied, so this is idempotent.
sed -i \
  -e 's|\(basePath: string = BASE_PATH\)) => {|\1): Promise<R> => {|' \
  -e 's|\(return axios\.request<T, R>(axiosRequestArgs)\);|\1 as Promise<R>;|' \
  "$COMMON"

if ! grep -q '): Promise<R> => {' "$COMMON"; then
  echo "ERROR: failed to annotate createRequestFunction in $COMMON" >&2
  echo "       The generated shape likely changed -- update this script." >&2
  exit 1
fi

echo "Applied axios response-type annotation to $COMMON"
