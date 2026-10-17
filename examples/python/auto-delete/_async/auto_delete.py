import asyncio

from daytona import AsyncDaytona
from daytona.v1 import CreateSandboxFromSnapshotParams


async def main():
    async with AsyncDaytona() as daytona_root:
        daytona = daytona_root.v1
        # Auto-delete is disabled by default
        sandbox1 = await daytona.create()
        print(sandbox1.auto_delete_interval)

        # Auto-delete after the Sandbox has been stopped for 1 hour
        await sandbox1.set_auto_delete_interval(60)
        print(sandbox1.auto_delete_interval)

        # Delete immediately upon stopping
        await sandbox1.set_auto_delete_interval(0)
        print(sandbox1.auto_delete_interval)

        # Disable auto-delete
        await sandbox1.set_auto_delete_interval(-1)
        print(sandbox1.auto_delete_interval)

        # Auto-delete after the Sandbox has been stopped for 1 day
        sandbox2 = await daytona.create(params=CreateSandboxFromSnapshotParams(auto_delete_interval=1440))
        print(sandbox2.auto_delete_interval)


if __name__ == "__main__":
    asyncio.run(main())
