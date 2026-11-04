from daytona import Daytona, AsyncDaytona

with Daytona() as api:
    api.create()

async def run():
    async with AsyncDaytona() as api:
        await api.create()
