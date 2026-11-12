#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
install_traps
rm -rf .next
install_sdk

export NEXT_TELEMETRY_DISABLED=1
BUILD_LOG="$PWD/next-build.log"
npm run build >"$BUILD_LOG" 2>&1
start_server_on_free_port "$PWD/next-start.log" "http://127.0.0.1:{PORT}/" npx next start -H 127.0.0.1 -p "{PORT}"
result="$(curl -sf -m 60 "http://127.0.0.1:$PORT/api/sandboxes")"
assert_response "$result"
