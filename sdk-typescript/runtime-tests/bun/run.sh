#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
require_binary bun "https://bun.sh"
rm -rf node_modules bun.lock bun.lockb
bun add --silent "$SDK_TARBALL"
assert_response "$(bun run test.ts)"
