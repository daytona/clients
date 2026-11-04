from daytona import Daytona

api = Daytona()
# daytona-v2: workflow `box`: move these v0 calls on it to v2 together: fs.list_files
# daytona-v2:   data: IDs do not carry over.
# daytona-v2:   docs: https://example.test/migrate#data
# daytona-v2: api.v2.create("python:3.12")
# daytona-v2:   differs: Choose an image.
# daytona-v2:   docs: https://example.test/migrate#client-create
box = api.v0.create()
# daytona-v2: box.fs.list(path)
# daytona-v2:   docs: https://example.test/migrate#fs-list
box.fs.list_files('.')
