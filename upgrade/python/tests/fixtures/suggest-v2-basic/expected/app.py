from daytona import Daytona

api = Daytona()
# daytona-v2: api.v2.create("python:3.12")
# daytona-v2:   differs: Choose an image.
# daytona-v2:   docs: https://example.test/migrate#client-create
api.v0.create()
