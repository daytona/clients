from daytona import Daytona
from daytona.v0 import Sandbox

api = Daytona()
box: Sandbox = api.v0.create()
api.v2.create('python:3.12')
api.close()
