#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
rm -rf dist
install_sdk
npx vite build --ssr src/ssr.ts --logLevel error
assert_response "$(node --input-type=module -e "const m = await import('./dist/ssr.js'); console.log(await m.run())")"
