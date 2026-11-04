from daytona.v0._sync.sandbox import Sandbox
from daytona.v0.common.errors import DaytonaError

def run(box: Sandbox):
    box.delete()
