import daytona
import daytona as old
from daytona import Daytona
from daytona.v0 import Sandbox

api = daytona.Daytona()
other = old.Daytona()
box: Sandbox = api.v0.create()
other.v0.create()
