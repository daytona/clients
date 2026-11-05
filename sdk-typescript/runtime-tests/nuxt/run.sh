#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
install_traps
rm -rf .nuxt .output
install_sdk

export NUXT_TELEMETRY_DISABLED=1
BUILD_LOG="$PWD/nuxt-build.log"
npm run build >"$BUILD_LOG" 2>&1
start_server_on_free_port "$PWD/nuxt-start.log" "http://127.0.0.1:{PORT}/" env HOST=127.0.0.1 PORT="{PORT}" node .output/server/index.mjs
result="$(curl -sf -m 60 "http://127.0.0.1:$PORT/api/sandboxes")"
assert_response "$result"
