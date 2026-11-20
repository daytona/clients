// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"sync"
	"time"
)

// InterpreterService runs code in stateful language kernels. Obtain it from
// Sandbox.Interpreter. Its handles and execution queues are concurrency-safe.
type InterpreterService struct {
	sandbox *Sandbox
	mu      sync.Mutex
	queues  map[string]*interpreterQueue
	now     func() time.Time
}

// Interpreter returns the sandbox's shared interpreter service.
func (s *Sandbox) Interpreter() *InterpreterService {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.interpreter == nil {
		s.interpreter = &InterpreterService{
			sandbox: s, queues: make(map[string]*interpreterQueue), now: time.Now,
		}
	}
	return s.interpreter
}

// InterpreterContext is an independent kernel's concurrency-safe handle.
// Obtain it from CreateContext, ConnectContext, ConnectContextJSON or ListContexts.
type InterpreterContext struct {
	service  *InterpreterService
	id       string
	language string
}

// ID returns the context's stable ID.
func (c *InterpreterContext) ID() string { return c.id }

// Language returns the context's language, an open server-defined vocabulary.
func (c *InterpreterContext) Language() string { return c.language }

// SandboxID returns the sandbox containing the context.
func (c *InterpreterContext) SandboxID() string { return c.service.sandbox.ID() }

// Identity returns a credential-free identity suitable for ConnectContextJSON.
func (c *InterpreterContext) Identity() InterpreterContextJSON {
	return InterpreterContextJSON{SandboxID: c.SandboxID(), ContextID: c.id, Language: c.language}
}

// Close deletes the context and its kernel state. ErrSessionNotFound is returned
// unchanged when the context has already disappeared.
func (c *InterpreterContext) Close(ctx context.Context) error {
	return c.service.DeleteContext(ctx, c.id)
}

// Run executes code in this context. A lost context returns ErrSessionNotFound;
// the SDK never recreates it or reruns the code automatically.
func (c *InterpreterContext) Run(
	ctx context.Context, code string, opts *InterpreterContextRunOptions,
) (*RunCodeResult, error) {
	options := RunCodeOptions{Context: c}
	if opts != nil {
		options.Cwd, options.Timeout = opts.Cwd, opts.Timeout
		options.Stdout, options.Stderr = opts.Stdout, opts.Stderr
		options.OnOutput, options.OnError, options.OnLog = opts.OnOutput, opts.OnError, opts.OnLog
	}
	return c.service.Run(ctx, code, &options)
}
