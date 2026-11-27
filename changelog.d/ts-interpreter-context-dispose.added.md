**TypeScript: `await using` for interpreter contexts** — `InterpreterContext`
implements `Symbol.asyncDispose`, so `await using ctx = await
sandbox.interpreter.createContext({ language: 'python' })` deletes the context
on scope exit, exactly as `ctx.close()` does.
