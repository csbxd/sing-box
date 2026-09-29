package sd

import (
	"context"
	"errors"
	"net/netip"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/common/json/badoption"

	"github.com/stretchr/testify/require"
)

type testProvider struct {
	calls    atomic.Int32
	discover func(context.Context, int32) (adapter.ServiceDiscoveryResult, error)
}

func (p *testProvider) Discover(ctx context.Context) (adapter.ServiceDiscoveryResult, error) {
	return p.discover(ctx, p.calls.Add(1))
}
func (p *testProvider) Close() error { return nil }

func result(address string) adapter.ServiceDiscoveryResult {
	return adapter.ServiceDiscoveryResult{Addresses: []netip.AddrPort{netip.MustParseAddrPort(address)}, TTL: 60}
}

func testManager(t *testing.T, options option.ServiceDiscoveryOptions, provider *testProvider) *Manager {
	t.Helper()
	m, err := NewManager(t.Context(), nil, options)
	require.NoError(t, err)
	m.providers["cf"] = provider
	t.Cleanup(func() { require.NoError(t, m.Close()) })
	return m
}

func expire(t *testing.T, m *Manager) {
	t.Helper()
	m.mu.Lock()
	defer m.mu.Unlock()
	element := m.cache["cf"]
	require.NotNil(t, element)
	entry := element.Value.(cacheEntry)
	entry.expires = time.Now().Add(-time.Second)
	element.Value = entry
}

func TestCacheAndRefresh(t *testing.T) {
	p := &testProvider{discover: func(_ context.Context, call int32) (adapter.ServiceDiscoveryResult, error) {
		if call == 1 {
			return result("192.0.2.1:1000"), nil
		}
		return result("192.0.2.2:2000"), nil
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{}, p)
	q := option.ServiceDiscoveryQueryOptions{Server: "cf"}
	first, err := m.Discover(t.Context(), q)
	require.NoError(t, err)
	first[0] = netip.AddrPort{}
	second, err := m.Discover(t.Context(), q)
	require.NoError(t, err)
	require.Equal(t, result("192.0.2.1:1000").Addresses, second)
	require.EqualValues(t, 1, p.calls.Load())
	expire(t, m)
	third, err := m.Discover(t.Context(), q)
	require.NoError(t, err)
	require.Equal(t, result("192.0.2.2:2000").Addresses, third)
	require.EqualValues(t, 2, p.calls.Load())
}

func TestCacheOptions(t *testing.T) {
	for _, tc := range []struct {
		name   string
		global option.ServiceDiscoveryOptions
		query  option.ServiceDiscoveryQueryOptions
		calls  int32
	}{
		{"query disable", option.ServiceDiscoveryOptions{}, option.ServiceDiscoveryQueryOptions{DisableCache: true}, 2},
		{"global disable", option.ServiceDiscoveryOptions{DisableCache: true}, option.ServiceDiscoveryQueryOptions{}, 2},
		{"TTL zero", option.ServiceDiscoveryOptions{}, option.ServiceDiscoveryQueryOptions{RewriteTTL: new(uint32)}, 2},
		{"TTL override", option.ServiceDiscoveryOptions{}, option.ServiceDiscoveryQueryOptions{RewriteTTL: ptrTTL(120)}, 1},
		{"disable expire", option.ServiceDiscoveryOptions{DisableExpire: true}, option.ServiceDiscoveryQueryOptions{}, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := &testProvider{discover: func(context.Context, int32) (adapter.ServiceDiscoveryResult, error) {
				return result("192.0.2.1:443"), nil
			}}
			m := testManager(t, tc.global, p)
			tc.query.Server = "cf"
			_, err := m.Discover(t.Context(), tc.query)
			require.NoError(t, err)
			if tc.global.DisableExpire {
				expire(t, m)
			}
			_, err = m.Discover(t.Context(), tc.query)
			require.NoError(t, err)
			require.Equal(t, tc.calls, p.calls.Load())
			if tc.query.RewriteTTL != nil && *tc.query.RewriteTTL > 0 {
				m.mu.Lock()
				entry := m.cache["cf"].Value.(cacheEntry)
				m.mu.Unlock()
				require.InDelta(t, 120, time.Until(entry.expires).Seconds(), 2)
			}
		})
	}
}

func ptrTTL(value uint32) *uint32 { return &value }

func TestOptimisticRefresh(t *testing.T) {
	for _, fail := range []bool{false, true} {
		name := "success"
		if fail {
			name = "failure"
		}
		t.Run(name, func(t *testing.T) {
			started, release := make(chan struct{}), make(chan struct{})
			p := &testProvider{discover: func(ctx context.Context, call int32) (adapter.ServiceDiscoveryResult, error) {
				if call == 1 {
					return result("192.0.2.1:1000"), nil
				}
				close(started)
				select {
				case <-ctx.Done():
					return adapter.ServiceDiscoveryResult{}, ctx.Err()
				case <-release:
				}
				if fail {
					return adapter.ServiceDiscoveryResult{}, errors.New("unavailable")
				}
				return result("192.0.2.2:2000"), nil
			}}
			m := testManager(t, option.ServiceDiscoveryOptions{Optimistic: &option.OptimisticDNSOptions{Enabled: true}}, p)
			q := option.ServiceDiscoveryQueryOptions{Server: "cf"}
			_, err := m.Discover(t.Context(), q)
			require.NoError(t, err)
			expire(t, m)
			old, err := m.Discover(t.Context(), q)
			require.NoError(t, err)
			require.Equal(t, result("192.0.2.1:1000").Addresses, old)
			<-started
			for range 10 {
				_, err = m.Discover(t.Context(), q)
				require.NoError(t, err)
			}
			require.EqualValues(t, 2, p.calls.Load())
			close(release)
			m.wg.Wait()
			m.mu.Lock()
			cached := m.cache["cf"].Value.(cacheEntry)
			m.mu.Unlock()
			if fail {
				require.Equal(t, old, cached.addresses)
			} else {
				require.Equal(t, result("192.0.2.2:2000").Addresses, cached.addresses)
			}
		})
	}
}

func TestConcurrentDiscoverAndCancellation(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	p := &testProvider{discover: func(ctx context.Context, call int32) (adapter.ServiceDiscoveryResult, error) {
		if call == 1 {
			close(started)
		}
		select {
		case <-ctx.Done():
			return adapter.ServiceDiscoveryResult{}, ctx.Err()
		case <-release:
			return result("192.0.2.1:443"), nil
		}
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{}, p)
	q := option.ServiceDiscoveryQueryOptions{Server: "cf"}
	var group sync.WaitGroup
	for range 20 {
		group.Go(func() {
			_, err := m.Discover(t.Context(), q)
			if err != nil {
				t.Error(err)
			}
		})
	}
	<-started
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	_, err := m.Discover(ctx, q)
	require.ErrorIs(t, err, context.Canceled)
	close(release)
	group.Wait()
	require.EqualValues(t, 1, p.calls.Load())
}

func TestStrictRefreshAndClose(t *testing.T) {
	p := &testProvider{discover: func(ctx context.Context, call int32) (adapter.ServiceDiscoveryResult, error) {
		if call == 1 {
			return result("192.0.2.1:443"), nil
		}
		return adapter.ServiceDiscoveryResult{}, errors.New("unavailable")
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{Optimistic: &option.OptimisticDNSOptions{Enabled: true}}, p)
	q := option.ServiceDiscoveryQueryOptions{Server: "cf", DisableOptimisticCache: true}
	_, err := m.Discover(t.Context(), q)
	require.NoError(t, err)
	expire(t, m)
	_, err = m.Discover(t.Context(), q)
	require.ErrorContains(t, err, "unavailable")
	require.NoError(t, m.Close())
	_, err = m.Discover(t.Context(), q)
	require.Error(t, err)
}

func TestStrategiesAndTimeout(t *testing.T) {
	p := &testProvider{discover: func(context.Context, int32) (adapter.ServiceDiscoveryResult, error) {
		return adapter.ServiceDiscoveryResult{Addresses: []netip.AddrPort{netip.MustParseAddrPort("192.0.2.1:1000"), netip.MustParseAddrPort("[2001:db8::1]:2000")}, TTL: 60}, nil
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{Strategy: option.DomainStrategy(C.DomainStrategyPreferIPv6)}, p)
	addresses, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
	require.NoError(t, err)
	require.True(t, addresses[0].Addr().Is6())
	addresses, err = m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf", Strategy: option.DomainStrategy(C.DomainStrategyIPv4Only)})
	require.NoError(t, err)
	require.Len(t, addresses, 1)
	require.True(t, addresses[0].Addr().Is4())
	slow := testManager(t, option.ServiceDiscoveryOptions{Timeout: badoption.Duration(time.Minute)}, &testProvider{discover: func(ctx context.Context, _ int32) (adapter.ServiceDiscoveryResult, error) {
		<-ctx.Done()
		return adapter.ServiceDiscoveryResult{}, ctx.Err()
	}})
	_, err = slow.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf", Timeout: badoption.Duration(time.Millisecond)})
	require.ErrorIs(t, err, context.DeadlineExceeded)
}

func TestInvalidGlobalOptions(t *testing.T) {
	for _, options := range []option.ServiceDiscoveryOptions{
		{Timeout: -1},
		{DisableCache: true, Optimistic: &option.OptimisticDNSOptions{Enabled: true}},
		{DisableExpire: true, Optimistic: &option.OptimisticDNSOptions{Enabled: true}},
		{Servers: []option.ServiceDiscoveryServerOptions{{Tag: "a", Type: "unknown"}}},
	} {
		_, err := NewManager(t.Context(), nil, options)
		require.Error(t, err)
	}
}

func TestCloseCancelsActiveDiscovery(t *testing.T) {
	started := make(chan struct{})
	p := &testProvider{discover: func(ctx context.Context, _ int32) (adapter.ServiceDiscoveryResult, error) {
		close(started)
		<-ctx.Done()
		return adapter.ServiceDiscoveryResult{}, ctx.Err()
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{}, p)
	done := make(chan error, 1)
	go func() {
		_, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
		done <- err
	}()
	<-started
	require.NoError(t, m.Close())
	require.ErrorIs(t, <-done, context.Canceled)
}

func TestRuntimeDependencyCycle(t *testing.T) {
	var m *Manager
	p := &testProvider{discover: func(ctx context.Context, _ int32) (adapter.ServiceDiscoveryResult, error) {
		_, err := m.Discover(ctx, option.ServiceDiscoveryQueryOptions{Server: "cf"})
		return adapter.ServiceDiscoveryResult{}, err
	}}
	m = testManager(t, option.ServiceDiscoveryOptions{}, p)
	_, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: "cf"})
	require.ErrorContains(t, err, "circular sd dependency")
}

func TestCacheIsolationAndEviction(t *testing.T) {
	p := &testProvider{discover: func(context.Context, int32) (adapter.ServiceDiscoveryResult, error) {
		return result("192.0.2.1:443"), nil
	}}
	m := testManager(t, option.ServiceDiscoveryOptions{}, p)
	m.capacity = 1
	m.providers["other"] = p
	for _, tag := range []string{"cf", "other", "cf"} {
		_, err := m.Discover(t.Context(), option.ServiceDiscoveryQueryOptions{Server: tag})
		require.NoError(t, err)
	}
	require.EqualValues(t, 3, p.calls.Load())
	require.Len(t, m.cache, 1)
}
