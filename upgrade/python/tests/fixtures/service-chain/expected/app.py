from daytona import Daytona

api = Daytona()
api.v0.snapshot.list()
api.v0.volume.get('data')
api.v0.secret.list()
api.v0.warm_pool.list()
create = api.v0.create
api.close()
