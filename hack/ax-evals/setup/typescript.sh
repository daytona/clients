# Build the TypeScript SDK and its generated clients, then install the packed
# tarballs into /app/tseval.
cd /opt/clients
corepack enable >/dev/null 2>&1 || { npm install --global corepack >/dev/null && corepack enable; }
t "yarn install"
retry yarn install --immutable >/tmp/yarn.log 2>&1 || { tail -n 30 /tmp/yarn.log; exit 1; }
t "nx build sdk-typescript"
NX_DAEMON=false NX_NO_CLOUD=true yarn nx run sdk-typescript:build --skip-nx-cache >/tmp/nx.log 2>&1 || { tail -n 40 /tmp/nx.log; exit 1; }
t "pack"
mkdir -p /opt/pkgs /app/tseval
for d in api-client toolbox-api-client analytics-api-client sdk-typescript; do
  (cd "dist/$d" && npm pack --silent --pack-destination /opt/pkgs >/dev/null)
done
cd /app/tseval
node - <<'JS'
const fs = require("fs")
const dependencies = {}
const overrides = {}
for (const dir of ["api-client", "toolbox-api-client", "analytics-api-client", "sdk-typescript"]) {
  const { name, version } = JSON.parse(fs.readFileSync(`/opt/clients/dist/${dir}/package.json`, "utf8"))
  const tarball = `${name.replace("@", "").replace("/", "-")}-${version}.tgz`
  const spec = `file:/opt/pkgs/${tarball}`
  if (!fs.existsSync(`/opt/pkgs/${tarball}`)) throw new Error(`missing ${tarball}`)
  overrides[name] = spec
  if (dir === "sdk-typescript") dependencies[name] = spec
}
fs.writeFileSync("package.json", JSON.stringify({ name: "tseval", private: true, type: "module", dependencies, overrides }, null, 2))
JS
t "npm install"
npm install --no-audit --no-fund 2>&1 | tail -n 3
npm ls --depth=0
t done
