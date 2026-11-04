export declare class Daytona {
  create(): Promise<Sandbox>;
  get(id: string): Promise<Sandbox>;
  list(): AsyncIterable<Sandbox>;
  delete(sandbox: Sandbox): Promise<void>;
  start(sandbox: Sandbox): Promise<void>;
  stop(sandbox: Sandbox): Promise<void>;
  fork(sandbox: Sandbox): Promise<Sandbox>;
  snapshot: { list(): Promise<string[]> };
}
export declare class Sandbox {
  delete(): Promise<void>;
  start(): Promise<void>;
  fs: { downloadFile(path: string): Promise<Uint8Array> };
}
