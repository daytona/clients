#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
rm -rf dist
install_sdk
npx vite build --ssr src/ssr.ts --logLevel error
# The SDK must be inside the bundle (vite.config.js), not imported from
# node_modules at run time.
if grep -qE "^import .* from [\"']@daytona/sdk[\"']|(import|require)\([\"']@daytona/sdk[\"']\)" dist/ssr.js; then
  echo "FAIL: dist/ssr.js imports @daytona/sdk instead of bundling it"
  exit 1
fi
result="$(node --input-type=module -e "const m = await import('./dist/ssr.js'); console.log(await m.run())")"
assert_response "$result"
