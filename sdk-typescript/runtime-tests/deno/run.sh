#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
require_binary deno "https://deno.com"
install_sdk
result="$(deno run --allow-env --allow-net=127.0.0.1 --allow-read --no-lock --no-check test.ts)"
assert_response "$result"
