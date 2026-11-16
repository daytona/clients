# ⚠️ `daytona_sdk` is deprecated

**This package is deprecated. Please migrate to the 0.x line of the [`daytona`](https://pypi.org/project/daytona/) package.**

```bash
pip uninstall daytona_sdk
pip install "daytona<1"
```

Then update your imports:

```python
import daytona  # instead of: import daytona_sdk
```

`daytona_sdk` currently ships the same code as `daytona` 0.x and will continue to
receive updates for a transition period so existing installs keep working.
However, it is being phased out and will eventually be discontinued. All active
development happens on the [`daytona`](https://pypi.org/project/daytona/)
package, so please migrate at your earliest convenience. Keep the `<1` pin:
`daytona` 2.x is a new major version with breaking changes.

See the [Daytona Python SDK documentation](https://www.daytona.io/docs/en/python-sdk/) for more information.
