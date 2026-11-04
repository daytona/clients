from daytona import Daytona, DaytonaConfig

# FIXME(daytona-upgrade): DaytonaConfig.use_deprecated_polling is not supported by SDK 2.x; review and remove it.
config = DaytonaConfig(api_key='secret', target='us', use_deprecated_polling=True)
api = Daytona(config)
api.v0.create()
