#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
require_binary func "npm i -g azure-functions-core-tools@4"
install_traps
rm -rf dist
install_sdk
npm run --silent build
cp runtime-checks.mjs dist/

cat >local.settings.json <<SETTINGS
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "",
    "FUNCTIONS_WORKER_RUNTIME": "node",
    "DAYTONA_V2_API_URL": "$DAYTONA_V2_API_URL",
    "DAYTONA_API_KEY": "$DAYTONA_API_KEY"
  }
}
SETTINGS
export FUNCTIONS_CORE_TOOLS_TELEMETRY_OPTOUT=1
WAIT_FOR_URL_SECONDS=120 start_server_on_free_port "$PWD/func-start.log" "http://127.0.0.1:{PORT}/" func start --port "{PORT}"
result="$(curl -sf -m 60 "http://127.0.0.1:$PORT/api/sandboxes")"
assert_response "$result"
