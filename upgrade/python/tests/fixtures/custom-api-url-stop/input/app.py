from daytona import Daytona, DaytonaConfig, Sandbox

api = Daytona(DaytonaConfig(api_url='https://private.example/api'))
box: Sandbox = api.create()
