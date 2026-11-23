// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"testing/synctest"

	"github.com/stretchr/testify/require"
)

func TestRunCode_whenImplicitAndExplicitSelectSameKernel(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		bound, err := sandbox.Interpreter().ConnectContext("code-python", "python")
		require.NoError(t, err)
		first, second := make(chan error, 1), make(chan error, 1)
		go func() {
			_, runErr := sandbox.RunCode(t.Context(), "implicit", nil)
			first <- runErr
		}()
		<-transport.started
		// When
		go func() {
			_, runErr := bound.Run(t.Context(), "explicit", nil)
			second <- runErr
		}()
		synctest.Wait()
		// Then
		require.EqualValues(t, 1, transport.posts.Load())
		close(transport.release)
		require.NoError(t, <-first)
		require.NoError(t, <-second)
	})
}

func TestRunCode_whenDifferentLanguagesProceedIndependently(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		first := make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(t.Context(), "python", nil)
			first <- err
		}()
		<-transport.started
		// When
		result, err := sandbox.RunCode(t.Context(), "javascript", &RunCodeOptions{Language: "javascript"})
		// Then
		require.NoError(t, err)
		require.Equal(t, "p2", result.Process.ProcessID)
		require.EqualValues(t, 2, transport.posts.Load())
		close(transport.release)
		require.NoError(t, <-first)
	})
}

func TestRunCode_whenClientClosesWithQueuedExecution(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		first, second := make(chan error, 1), make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(t.Context(), "first", nil)
			first <- err
		}()
		<-transport.started
		go func() {
			_, err := sandbox.RunCode(t.Context(), "second", nil)
			second <- err
		}()
		synctest.Wait()
		// When
		err := sandbox.client.Close()
		// Then
		require.NoError(t, err)
		require.ErrorIs(t, <-first, ErrClientClosed)
		require.ErrorIs(t, <-second, ErrClientClosed)
		require.EqualValues(t, 1, transport.posts.Load())
	})
}

func TestRunCode_whenQueuedContextDeadlineExpires(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		// Given
		transport := &interpreterQueueTransport{started: make(chan struct{}), release: make(chan struct{})}
		sandbox := interpreterQueueSandbox(t, transport)
		first := make(chan error, 1)
		go func() {
			_, err := sandbox.RunCode(t.Context(), "first", nil)
			first <- err
		}()
		<-transport.started
		ctx, cancel := context.WithDeadline(t.Context(), sandbox.Interpreter().now())
		defer cancel()
		// When
		_, err := sandbox.RunCode(ctx, "second", nil)
		// Then
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.EqualValues(t, 1, transport.posts.Load())
		close(transport.release)
		require.NoError(t, <-first)
	})
}
