from daytona import Daytona, AsyncDaytona

with Daytona() as api:
    api.v0.create()

async def run():
    async with AsyncDaytona() as api:
        await api.v0.create()
