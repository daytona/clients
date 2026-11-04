from daytona import Daytona

class Worker:
    def run(self):
        return self.client.create()

    def __init__(self):
        self.client = Daytona()
