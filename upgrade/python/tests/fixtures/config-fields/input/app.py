from daytona import Daytona, DaytonaConfig

config = DaytonaConfig(api_key='secret', target='us', use_deprecated_polling=True)
api = Daytona(config)
api.create()
