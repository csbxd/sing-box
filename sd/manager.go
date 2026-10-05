package sd

import (
	"container/list"
	"context"
	"net/netip"
	"slices"
	"sync"
	"time"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	E "github.com/sagernet/sing/common/exceptions"
)

var _ adapter.ServiceDiscovery = (*Manager)(nil)

type cacheEntry struct {
	tag       string
	addresses []netip.AddrPort
	expires   time.Time
}

type flightKey struct {
	tag     string
	timeout time.Duration
}
type flight struct {
	done   chan struct{}
	result adapter.ServiceDiscoveryResult
	err    error
}
type discoveryContextKey struct{}

type Manager struct {
	ctx               context.Context
	cancel            context.CancelFunc
	logger            log.ContextLogger
	options           option.ServiceDiscoveryOptions
	providers         map[string]adapter.ServiceDiscoveryProvider
	optimisticTimeout time.Duration
	timeout           time.Duration
	capacity          int
	mu                sync.Mutex
	closed            bool
	lifecycleStarted  bool
	cache             map[string]*list.Element
	lru               list.List
	flights           map[flightKey]*flight
	wg                sync.WaitGroup
}

func NewManager(ctx context.Context, logger log.ContextLogger, options option.ServiceDiscoveryOptions) (*Manager, error) {
	if options.Timeout < 0 {
		return nil, E.New("sd: timeout must not be negative")
	}
	var optimisticTimeout time.Duration
	if options.Optimistic != nil && options.Optimistic.Enabled {
		if options.DisableCache || options.DisableExpire {
			return nil, E.New("sd: optimistic conflicts with disable_cache and disable_expire")
		}
		optimisticTimeout = time.Duration(options.Optimistic.Timeout)
		if optimisticTimeout < 0 {
			return nil, E.New("sd: optimistic timeout must not be negative")
		}
		if optimisticTimeout == 0 {
			optimisticTimeout = 3 * 24 * time.Hour
		}
	}
	ctx, cancel := context.WithCancel(ctx)
	m := &Manager{
		ctx: ctx, cancel: cancel, logger: logger, options: options,
		providers:         make(map[string]adapter.ServiceDiscoveryProvider),
		optimisticTimeout: optimisticTimeout, timeout: time.Duration(options.Timeout),
		capacity: int(max(options.CacheCapacity, 1024)),
		cache:    make(map[string]*list.Element), flights: make(map[flightKey]*flight),
	}
	if m.timeout == 0 {
		m.timeout = C.DNSTimeout
	}
	for _, server := range options.Servers {
		if server.Tag == "" || m.HasServer(server.Tag) {
			m.Close()
			return nil, E.New("sd: empty or duplicate server tag: ", server.Tag)
		}
		var provider adapter.ServiceDiscoveryProvider
		var err error
		switch server.Type {
		case "cloudflare":
			provider, err = newCloudflare(ctx, server)
		default:
			err = E.New("unknown sd type: ", server.Type)
		}
		if err != nil {
			m.Close()
			return nil, E.Cause(err, "initialize sd/", server.Tag)
		}
		m.providers[server.Tag] = provider
	}
	return m, nil
}

func (m *Manager) Name() string              { return "service discovery" }
func (m *Manager) HasServer(tag string) bool { _, ok := m.providers[tag]; return ok }

func (m *Manager) Start(stage adapter.StartStage, scope *adapter.Scope) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return E.New("sd is closed")
	}
	if stage != adapter.StartStateInitialize || m.lifecycleStarted {
		return nil
	}
	if err := scope.Context().Err(); err != nil {
		return err
	}
	if err := m.ctx.Err(); err != nil {
		return err
	}
	// Keep the constructor context used by providers, and connect it to the
	// lifecycle scope so cancellation reaches in-flight and optimistic queries.
	stop := context.AfterFunc(scope.Context(), m.cancel)
	scope.Add(func() error {
		stop()
		return m.Close()
	})
	m.lifecycleStarted = true
	return nil
}

func (m *Manager) Close() error {
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return nil
	}
	m.closed = true
	m.cancel()
	m.mu.Unlock()
	m.wg.Wait()
	var errs []error
	for _, provider := range m.providers {
		errs = append(errs, provider.Close())
	}
	return E.Errors(errs...)
}

func (m *Manager) Discover(ctx context.Context, options option.ServiceDiscoveryQueryOptions) ([]netip.AddrPort, error) {
	if !m.HasServer(options.Server) {
		return nil, E.New("sd server not found: ", options.Server)
	}
	if options.Timeout < 0 {
		return nil, E.New("sd: timeout must not be negative")
	}
	path, _ := ctx.Value(discoveryContextKey{}).([]string)
	if slices.Contains(path, options.Server) {
		return nil, E.New("circular sd dependency: ", options.Server)
	}
	ctx = context.WithValue(ctx, discoveryContextKey{}, append(slices.Clone(path), options.Server))
	timeout := time.Duration(options.Timeout)
	if timeout == 0 {
		timeout = m.timeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	stop := context.AfterFunc(m.ctx, cancel)
	defer stop()
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	key := flightKey{options.Server, timeout}
	disableCache := m.options.DisableCache || options.DisableCache
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		return nil, E.New("sd is closed")
	}
	if !disableCache {
		if element, ok := m.cache[options.Server]; ok {
			entry := element.Value.(cacheEntry)
			now := time.Now()
			fresh := m.options.DisableExpire || now.Before(entry.expires)
			stale := !fresh && m.optimisticTimeout > 0 && now.Before(entry.expires.Add(m.optimisticTimeout))
			if fresh || stale && !options.DisableOptimisticCache {
				m.lru.MoveToFront(element)
				if stale {
					if _, running := m.flights[key]; !running {
						pending := &flight{done: make(chan struct{})}
						m.flights[key] = pending
						m.wg.Add(1)
						go m.refresh(options, key, pending)
					}
				}
				m.mu.Unlock()
				return m.selectAddresses(entry.addresses, options)
			}
			if !stale {
				m.lru.Remove(element)
				delete(m.cache, options.Server)
			}
		}
		if pending, running := m.flights[key]; running {
			m.mu.Unlock()
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-pending.done:
				if pending.err != nil {
					return nil, pending.err
				}
				return m.selectAddresses(pending.result.Addresses, options)
			}
		}
	}
	pending := &flight{done: make(chan struct{})}
	if !disableCache {
		m.flights[key] = pending
	}
	m.wg.Add(1)
	m.mu.Unlock()
	result, err := m.fetch(ctx, options)
	m.finish(key, pending, result, err, !disableCache)
	if err != nil {
		return nil, err
	}
	return m.selectAddresses(result.Addresses, options)
}

func (m *Manager) refresh(options option.ServiceDiscoveryQueryOptions, key flightKey, pending *flight) {
	ctx, cancel := context.WithTimeout(m.ctx, key.timeout)
	defer cancel()
	ctx = context.WithValue(ctx, discoveryContextKey{}, []string{options.Server})
	result, err := m.fetch(ctx, options)
	if err != nil && m.logger != nil {
		m.logger.DebugContext(ctx, "refresh sd/", options.Server, ": ", err)
	}
	m.finish(key, pending, result, err, true)
}

func (m *Manager) fetch(ctx context.Context, options option.ServiceDiscoveryQueryOptions) (adapter.ServiceDiscoveryResult, error) {
	result, err := m.providers[options.Server].Discover(ctx)
	if err == nil {
		err = ctx.Err()
	}
	if err != nil {
		return adapter.ServiceDiscoveryResult{}, err
	}
	if len(result.Addresses) == 0 {
		return adapter.ServiceDiscoveryResult{}, E.New("sd returned no addresses")
	}
	for _, address := range result.Addresses {
		if !address.IsValid() || address.Port() == 0 {
			return adapter.ServiceDiscoveryResult{}, E.New("sd returned invalid address")
		}
	}
	result.Addresses = slices.Clone(result.Addresses)
	if options.RewriteTTL != nil {
		result.TTL = *options.RewriteTTL
	}
	return result, nil
}

func (m *Manager) finish(key flightKey, pending *flight, result adapter.ServiceDiscoveryResult, err error, cache bool) {
	m.mu.Lock()
	if cache {
		delete(m.flights, key)
		if err == nil && !m.closed && result.TTL > 0 {
			entry := cacheEntry{key.tag, result.Addresses, time.Now().Add(time.Duration(result.TTL) * time.Second)}
			if old, ok := m.cache[key.tag]; ok {
				m.lru.Remove(old)
			}
			m.cache[key.tag] = m.lru.PushFront(entry)
			if m.lru.Len() > m.capacity {
				last := m.lru.Back()
				delete(m.cache, last.Value.(cacheEntry).tag)
				m.lru.Remove(last)
			}
		}
	}
	pending.result, pending.err = result, err
	close(pending.done)
	m.mu.Unlock()
	m.wg.Done()
}

func (m *Manager) selectAddresses(addresses []netip.AddrPort, options option.ServiceDiscoveryQueryOptions) ([]netip.AddrPort, error) {
	strategy := options.Strategy
	if strategy == 0 {
		strategy = m.options.Strategy
	}
	var selected []netip.AddrPort
	for _, address := range addresses {
		if strategy == option.DomainStrategy(C.DomainStrategyIPv4Only) && !address.Addr().Is4() || strategy == option.DomainStrategy(C.DomainStrategyIPv6Only) && !address.Addr().Is6() {
			continue
		}
		selected = append(selected, address)
	}
	if len(selected) == 0 {
		return nil, E.New("sd returned no addresses for selected strategy")
	}
	if strategy == option.DomainStrategy(C.DomainStrategyPreferIPv4) || strategy == option.DomainStrategy(C.DomainStrategyPreferIPv6) {
		preferIPv6 := strategy == option.DomainStrategy(C.DomainStrategyPreferIPv6)
		slices.SortStableFunc(selected, func(a, b netip.AddrPort) int {
			if a.Addr().Is6() == b.Addr().Is6() {
				return 0
			}
			if a.Addr().Is6() == preferIPv6 {
				return -1
			}
			return 1
		})
	}
	return selected, nil
}
