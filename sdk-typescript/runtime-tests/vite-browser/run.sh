#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
rm -rf dist
install_sdk
# The Chromium build of the pinned playwright version (CI installs it ahead
# with the same version; locally this downloads it once).
if ! npx playwright install chromium >playwright-install.log 2>&1; then
  cat playwright-install.log
  echo "SKIP: could not install Playwright Chromium"
  exit 0
fi
npx tsc -p tsconfig.json
npx vite build --logLevel error
result="$(node test-browser.mjs)"
assert_response "$result"
