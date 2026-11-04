import daytona_sdk
import daytona_sdk as old
from daytona_sdk import Daytona, Sandbox

api = daytona_sdk.Daytona()
other = old.Daytona()
box: Sandbox = api.create()
other.create()
