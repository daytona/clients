#!/usr/bin/env bash
set -euo pipefail

# Sets custom User-Agent in generated Java API clients using the project version.
# Usage: postprocess.sh <project-root> <client-name>

if [ $# -lt 2 ]; then
  echo "Usage: $0 <project-root> <client-name>" >&2
  exit 1
fi

PROJECT_ROOT="$1"
CLIENT_NAME="$2"
API_CLIENT=$(find "$PROJECT_ROOT/src" -name "ApiClient.java" -path "*/client/ApiClient.java" | head -1)

if [ -z "$API_CLIENT" ] || [ ! -f "$API_CLIENT" ]; then
  echo "ERROR: ApiClient.java not found in $PROJECT_ROOT" >&2
  exit 1
fi

VERSION=$(grep "^version" "$PROJECT_ROOT/build.gradle" | sed "s/version = '//" | sed "s/'//")
sed -i "s|setUserAgent(\"OpenAPI-Generator/[^\"]*\")|setUserAgent(\"${CLIENT_NAME}/${VERSION}\")|" "$API_CLIENT"

# OpenAPI Generator 7.21.0 emits a primitive cast for these array branches.
# Preserve the JsonArray; scalar branches keep their existing adapters.
if [ "$CLIENT_NAME" = api-client-java ]; then
  for RESOURCE in Cpu Memory Disk; do
    MODEL_FILE="${API_CLIENT%/*}/model/CreateSandbox${RESOURCE}.java"
    if [ ! -f "$MODEL_FILE" ]; then
      echo "ERROR: Required resource model missing: $MODEL_FILE" >&2
      exit 1
    fi
    sed -i 's/JsonPrimitive primitive = adapterListInteger\.toJsonTree((List<Integer>)value\.getActualInstance())\.getAsJsonPrimitive();/JsonElement primitive = adapterListInteger.toJsonTree((List<Integer>)value.getActualInstance());/' "$MODEL_FILE"
    if ! grep -Fq 'JsonElement primitive = adapterListInteger.toJsonTree((List<Integer>)value.getActualInstance());' "$MODEL_FILE" || grep -Fq 'adapterListInteger.toJsonTree((List<Integer>)value.getActualInstance()).getAsJsonPrimitive()' "$MODEL_FILE"; then
      echo "ERROR: Resource-array serialization needs review after generator drift: $MODEL_FILE" >&2
      exit 1
    fi
  done
fi

echo "Postprocessed Java client at $PROJECT_ROOT (User-Agent: ${CLIENT_NAME}/${VERSION})"
