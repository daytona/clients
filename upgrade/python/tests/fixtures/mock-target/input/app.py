from unittest.mock import patch
from daytona import Daytona

patch('daytona.Sandbox')
patch('daytona.Daytona.create')
patch('daytona_sdk._sync.sandbox.Sandbox')
