# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Install the Python SDK (`daytona`) from the checked-out source.
cd /opt/clients
t "install sdk-python from source"
retry python3 -m pip install --quiet --break-system-packages --root-user-action=ignore ./sdk-python
python3 -c "import importlib.metadata as m, daytona.v2; print('daytona', m.version('daytona'))"
t done
