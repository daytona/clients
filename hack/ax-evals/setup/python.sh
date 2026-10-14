# Install the Python SDK and its generated clients from the checked-out source.
cd /opt/clients
PIP="python3 -m pip install --quiet --break-system-packages --root-user-action=ignore"
t "install third-party dependencies"
python3 - <<'PY' >/tmp/deps.txt
import glob
import tomllib

deps: set[str] = set()
for path in glob.glob("*api-client-python*/pyproject.toml") + ["sdk-python/pyproject.toml"]:
    with open(path, "rb") as f:
        for dep in tomllib.load(f).get("project", {}).get("dependencies", []):
            if not dep.replace("_", "-").startswith("daytona"):
                deps.add(dep.replace(" (", "").replace(")", ""))
print("\n".join(sorted(deps)))
PY
retry $PIP -r /tmp/deps.txt
t "install daytona packages from source"
retry $PIP --no-deps ./api-client-python ./api-client-python-async ./toolbox-api-client-python \
  ./toolbox-api-client-python-async ./analytics-api-client-python ./analytics-api-client-python-async ./sdk-python
python3 -c "import daytona, importlib.metadata as m; print('daytona', m.version('daytona'))"
t done
