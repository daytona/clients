# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Build the TypeScript SDK (`@daytona/sdk`) from the checked-out source and install
# the packed tarball into /app/tseval.
install_node
cd /opt/clients/sdk-typescript
t "npm ci"
retry npm ci --no-audit --no-fund >/tmp/npm-ci.log 2>&1 || { tail -n 30 /tmp/npm-ci.log; exit 1; }
t "build"
npm run build >/tmp/build.log 2>&1 || { tail -n 40 /tmp/build.log; exit 1; }
t "pack"
$SUDO mkdir -p /opt/pkgs
$SUDO chown "$(id -u):$(id -g)" /opt/pkgs
TARBALL=$(npm pack --silent --pack-destination /opt/pkgs | tail -n 1)
$SUDO mkdir -p /app/tseval
$SUDO chown "$(id -u):$(id -g)" /app/tseval
cd /app/tseval
cat >package.json <<JSON
{ "name": "tseval", "private": true, "type": "module", "dependencies": { "@daytona/sdk": "file:/opt/pkgs/${TARBALL}" } }
JSON
t "npm install"
retry npm install --no-audit --no-fund 2>&1 | tail -n 3
npm ls --depth=0
node -e "import('@daytona/sdk').then((m) => console.log('@daytona/sdk exports', Object.keys(m).length))"
t done
