package sd

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"

	"github.com/stretchr/testify/require"
)

type lifecycleTestProvider struct {
	discover func(context.Context) (adapter.ServiceDiscoveryResult, error)
	active   atomic.Int32
	closes   atomic.Int32
	early    atomic.Bool
	closeErr error
}

func (p *lifecycleTestProvider) Discover(ctx context.Context) (adapter.ServiceDiscoveryResult, error) {
	p.active.Add(1)
	defer p.active.Add(-1)
	return p.discover(ctx)
}

func (p *lifecycleTestProvider) Close() error {
	if p.active.Load() != 0 {
		p.early.Store(true)
	}
	p.closes.Add(1)
	return p.closeErr
}

func newLifecycleTestManager(t *testing.T, options option.ServiceDiscoveryOptions, p *lifecycleTestProvider) *Manager {
	t.Helper()
	m, err := NewManager(t.Context(), nil, options)
	require.NoError(t, err)
	m.providers["cf"] = p
	t.Cleanup(func() { m.Close() })
	return m
}

func waitLifecycleSignal(t *testing.T, signal <-chan struct{}) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for discovery")
	}
}

func waitLifecycleError(t *testing.T, result <-chan error) error {
	t.Helper()
	select {
	case err := <-result:
		return err
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for cancellation")
		return nil
	}
}

func TestManagerScopeLifecycle(t *testing.T) {
	p := &lifecycleTestProvider{discover: func(context.Context) (adapter.ServiceDiscoveryResult, error) {
		return result("192.0.2.1:8443"), nil
	}}
	m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{}, p)
	scope := adapter.NewScope(t.Context(), log.NewNOPFactory().Logger())
	t.Cleanup(func() { scope.Close() })
	for range 2 {
		for _, stage := range adapter.ListStartStages {
			require.NoError(t, scope.Start(m.Name(), m, stage))
		}
	}
	addresses, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
	require.NoError(t, err)
	require.Equal(t, result("192.0.2.1:8443").Addresses, addresses)
	require.NoError(t, scope.Close())
	require.NoError(t, scope.Close())
	require.NoError(t, m.Close())
	require.EqualValues(t, 1, p.closes.Load())
	require.False(t, p.early.Load())
	require.ErrorIs(t, m.ctx.Err(), context.Canceled)
	require.Error(t, m.Start(adapter.StartStateInitialize, scope))
	_, err = m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
	require.Error(t, err)
}

func TestManagerCloseBeforeStart(t *testing.T) {
	p := &lifecycleTestProvider{}
	m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{}, p)
	require.NoError(t, m.Close())
	require.NoError(t, m.Close())
	scope := adapter.NewScope(t.Context(), log.NewNOPFactory().Logger())
	t.Cleanup(func() { scope.Close() })
	for _, stage := range adapter.ListStartStages {
		require.ErrorContains(t, m.Start(stage, scope), "sd is closed")
	}
	require.EqualValues(t, 1, p.closes.Load())
}

func TestManagerRejectsCanceledScope(t *testing.T) {
	p := &lifecycleTestProvider{}
	m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{}, p)
	ctx, cancel := context.WithCancel(t.Context())
	scope := adapter.NewScope(ctx, log.NewNOPFactory().Logger())
	t.Cleanup(func() { scope.Close() })
	cancel()
	require.ErrorIs(t, m.Start(adapter.StartStateInitialize, scope), context.Canceled)
	require.False(t, m.lifecycleStarted)
	require.NoError(t, m.Close())
	require.EqualValues(t, 1, p.closes.Load())
}

func TestManagerScopeCancellation(t *testing.T) {
	for _, parentCancellation := range []bool{false, true} {
		name := "scope close"
		if parentCancellation {
			name = "parent cancellation"
		}
		t.Run(name, func(t *testing.T) {
			started := make(chan struct{})
			p := &lifecycleTestProvider{discover: func(ctx context.Context) (adapter.ServiceDiscoveryResult, error) {
				close(started)
				<-ctx.Done()
				return adapter.ServiceDiscoveryResult{}, ctx.Err()
			}}
			// The manager constructor context is deliberately independent of the
			// scope parent: the lifecycle must bridge cancellation between them.
			m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{}, p)
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			scope := adapter.NewScope(ctx, log.NewNOPFactory().Logger())
			t.Cleanup(func() { scope.Close() })
			require.NoError(t, scope.Start(m.Name(), m, adapter.StartStateInitialize))
			done := make(chan error, 1)
			go func() {
				_, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
				done <- err
			}()
			waitLifecycleSignal(t, started)
			if parentCancellation {
				cancel()
			} else {
				closed := make(chan error, 1)
				go func() { closed <- scope.Close() }()
				require.NoError(t, waitLifecycleError(t, closed))
			}
			require.ErrorIs(t, waitLifecycleError(t, done), context.Canceled)
			require.NoError(t, scope.Close())
			require.EqualValues(t, 1, p.closes.Load())
			require.False(t, p.early.Load())
			require.Empty(t, m.flights)
		})
	}
}

func TestManagerScopeCancelsOptimisticRefresh(t *testing.T) {
	started := make(chan struct{})
	var calls atomic.Int32
	p := &lifecycleTestProvider{discover: func(ctx context.Context) (adapter.ServiceDiscoveryResult, error) {
		if calls.Add(1) == 1 {
			return result("192.0.2.1:8443"), nil
		}
		close(started)
		<-ctx.Done()
		return adapter.ServiceDiscoveryResult{}, ctx.Err()
	}}
	m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{
		Optimistic: &option.OptimisticDNSOptions{Enabled: true},
	}, p)
	scope := adapter.NewScope(t.Context(), log.NewNOPFactory().Logger())
	t.Cleanup(func() { scope.Close() })
	require.NoError(t, scope.Start(m.Name(), m, adapter.StartStateInitialize))
	query := option.ServiceDiscoveryQueryOptions{Server: "cf"}
	first, err := m.Discover(t.Context(), query)
	require.NoError(t, err)
	expire(t, m)
	cached, err := m.Discover(t.Context(), query)
	require.NoError(t, err)
	require.Equal(t, first, cached)
	waitLifecycleSignal(t, started)
	closed := make(chan error, 1)
	go func() { closed <- scope.Close() }()
	require.NoError(t, waitLifecycleError(t, closed))
	require.EqualValues(t, 2, calls.Load())
	require.EqualValues(t, 1, p.closes.Load())
	require.False(t, p.early.Load())
	require.Empty(t, m.flights)
	require.ErrorIs(t, m.ctx.Err(), context.Canceled)
}

type failingSDLifecycle struct {
	err error
}

func (f *failingSDLifecycle) Start(adapter.StartStage, *adapter.Scope) error {
	return f.err
}

func TestManagerScopeStartupFailureCleanup(t *testing.T) {
	closeErr := errors.New("provider close failure")
	p := &lifecycleTestProvider{closeErr: closeErr}
	m := newLifecycleTestManager(t, option.ServiceDiscoveryOptions{}, p)
	scope := adapter.NewScope(t.Context(), log.NewNOPFactory().Logger())
	t.Cleanup(func() { scope.Close() })
	require.NoError(t, scope.Start(m.Name(), m, adapter.StartStateInitialize))
	startErr := errors.New("later component failed")
	err := scope.Start("failing component", &failingSDLifecycle{err: startErr}, adapter.StartStateInitialize)
	require.ErrorIs(t, err, startErr)
	// Box closes its scope on startup errors; verify that this also reports
	// provider cleanup errors and never closes a provider twice.
	require.ErrorIs(t, scope.Close(), closeErr)
	require.NoError(t, scope.Close())
	require.NoError(t, m.Close())
	require.EqualValues(t, 1, p.closes.Load())
	require.ErrorIs(t, m.ctx.Err(), context.Canceled)
}
