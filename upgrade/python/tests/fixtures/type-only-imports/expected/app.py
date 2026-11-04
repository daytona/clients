from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from daytona import Daytona
    from daytona.v0 import Sandbox

def run(api: 'Daytona') -> 'Sandbox':
    return api.v0.create()
