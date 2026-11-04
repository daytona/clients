from daytona._sync.sandbox import Sandbox
from daytona.common.errors import DaytonaError

def run(box: Sandbox):
    box.delete()
