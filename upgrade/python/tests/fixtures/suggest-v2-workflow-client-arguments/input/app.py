from daytona import Daytona

daytona = Daytona()
sandbox = daytona.v0.create()
sandbox.set_labels({"env": "dev"})
daytona.v0.stop(sandbox)
daytona.v0.start(sandbox)
daytona.v0.delete(sandbox)
