#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
rm -rf dist
install_sdk
npm run --silent build
result="$(node invoke.cjs)"
assert_response "$result"
