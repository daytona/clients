#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
rm -rf dist
install_sdk
npm run --silent build
# Outside the working copy, so Node cannot resolve anything from its
# node_modules: the bundle must be self-contained.
standalone="$(mktemp -d)"
trap 'rm -rf "$standalone"' EXIT
cp dist/test.cjs "$standalone/"
result="$(cd "$standalone" && node test.cjs)"
assert_response "$result"
