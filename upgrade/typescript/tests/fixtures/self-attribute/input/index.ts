import { Daytona } from '@daytona/sdk';
class Service {
  private client: Daytona;
  constructor() { this.client = new Daytona(); }
  async work() {
    return this.client.create();
  }
}
class Injected {
  constructor(private client: Daytona) {}
  work() { return this.client.list(); }
}
