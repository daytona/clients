#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
client_dir="$fixture/src/main/java/io/daytona/api/client"
mkdir -p "$client_dir/model"
printf "version = '0.0.0-dev'\n" > "$fixture/build.gradle"
printf 'setUserAgent("OpenAPI-Generator/7.21.0");\n' > "$client_dir/ApiClient.java"
for resource in Cpu Memory Disk; do
  printf 'JsonPrimitive primitive = adapterListInteger.toJsonTree((List<Integer>)value.getActualInstance()).getAsJsonPrimitive();\n' > "$client_dir/model/CreateSandbox${resource}.java"
done
bash "$script_dir/postprocess.sh" "$fixture" api-client-java
# A successful patch stays safe on a second pass.
bash "$script_dir/postprocess.sh" "$fixture" api-client-java
printf 'unexpected generator output\n' > "$client_dir/model/CreateSandboxCpu.java"
if bash "$script_dir/postprocess.sh" "$fixture" api-client-java; then
  echo 'ERROR: generator drift went undetected' >&2
  exit 1
fi
rm "$client_dir/model/CreateSandboxCpu.java"
if bash "$script_dir/postprocess.sh" "$fixture" api-client-java; then
  echo 'ERROR: a missing resource model went undetected' >&2
  exit 1
fi
# Other generated Java clients do not contain sandbox-create resource models.
bash "$script_dir/postprocess.sh" "$fixture" toolbox-api-client-java
echo 'Java postprocess checks passed'
