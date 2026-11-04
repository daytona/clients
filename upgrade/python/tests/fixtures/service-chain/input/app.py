from daytona import Daytona

api = Daytona()
api.snapshot.list()
api.volume.get('data')
api.secret.list()
api.warm_pool.list()
create = api.create
api.close()
