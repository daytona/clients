#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# shellcheck source-path=SCRIPTDIR/.. source=lib.sh
source "$RUNTIME_LIB"
install_traps
install_sdk

# The worker gets its configuration as text bindings; with nodejs_compat and a
# compatibility date from 2025-04-01 on, workerd also exposes them on
# process.env, where the SDK reads DAYTONA_V2_API_URL. Outbound fetches may reach
# local addresses (the mock API).
cat >config.capnp <<CAPNP
using Workerd = import "/workerd/workerd.capnp";

const config :Workerd.Config = (
  services = [
    (name = "main", worker = .worker),
    (name = "internet", network = (allow = ["public", "private", "local"])),
  ],
  sockets = [(name = "http", address = "127.0.0.1:0", http = (), service = "main")],
);

const worker :Workerd.Worker = (
  modules = [(name = "worker.js", esModule = embed "dist/worker.js")],
  compatibilityDate = "2025-09-01",
  compatibilityFlags = ["nodejs_compat"],
  bindings = [
    (name = "DAYTONA_V2_API_URL", text = "$DAYTONA_V2_API_URL"),
    (name = "DAYTONA_API_KEY", text = "$DAYTONA_API_KEY"),
  ],
);
CAPNP

run_worker() {
  rm -rf dist
  npm run --silent build
  echo "axios $(node -p "require(require.resolve('axios/package.json', { paths: [require.resolve('@daytona/sdk')] })).version")"
  start_server_on_free_port "$PWD/workerd.log" "http://127.0.0.1:{PORT}/" npx workerd serve config.capnp --socket-addr "http=127.0.0.1:{PORT}"
  result="$(curl -sf -m 60 "http://127.0.0.1:$PORT/checks")"
  assert_response "$result"
  stop_server
}

# The axios release npm resolves today, then axios 1.20, which sends
# `cache: 'default'` on every fetch (workerd rejects it). The SDK's axios
# range allows 1.20, so an application's lockfile can pin it.
run_worker
npm install --silent axios@1.20.0
run_worker
