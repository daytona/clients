from daytona import Daytona

api = Daytona()
box = api.v0.create()
box.fs.list_files('.')
