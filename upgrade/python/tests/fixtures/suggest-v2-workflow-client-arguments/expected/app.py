from daytona import Daytona

daytona = Daytona()
# daytona-v2: workflow `sandbox`: these calls require a different lifecycle workflow in SDK 2.x; keep this workflow on .v0: daytona.v0.stop, daytona.v0.start
# daytona-v2:   data: IDs do not carry over.
# daytona-v2:   docs: https://example.test/migrate#data
# daytona-v2: daytona.v2.create("python:3.12")
# daytona-v2:   differs: Choose an image.
# daytona-v2:   docs: https://example.test/migrate#client-create
sandbox = daytona.v0.create()
sandbox.set_labels({"env": "dev"})
daytona.v0.stop(sandbox)
daytona.v0.start(sandbox)
daytona.v0.delete(sandbox)
