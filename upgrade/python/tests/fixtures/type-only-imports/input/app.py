from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daytona import Daytona, Sandbox

def run(api: 'Daytona') -> 'Sandbox':
    return api.create()
