package sd

import (
	"context"
	"net"
	"net/http"
	"net/netip"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/dns"
	"github.com/sagernet/sing-box/dns/transport/hosts"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/common/json/badjson"
	"github.com/sagernet/sing/common/json/badoption"
	"github.com/sagernet/sing/service"

	"github.com/stretchr/testify/require"
)

// Run explicitly with SING_BOX_TEST_CF_* environment variables. Never writes to Cloudflare.
func TestLiveCloudflareDiscovery(t *testing.T) {
	token := os.Getenv("SING_BOX_TEST_CF_TOKEN")
	if token == "" {
		t.Skip("live credentials not supplied")
	}
	ctx, cancel := context.WithTimeout(t.Context(), 20*time.Second)
	defer cancel()
	addresses, err := net.DefaultResolver.LookupNetIP(ctx, "ip4", cloudflareAPIHost)
	require.NoError(t, err)
	registry := dns.NewTransportRegistry()
	hosts.RegisterTransport(registry)
	logger := log.NewNOPFactory()
	dnsManager := dns.NewTransportManager(registry, nil, "bootstrap")
	ctx = service.ContextWith[adapter.DNSTransportManager](ctx, dnsManager)
	router, err := dns.NewRouter(ctx, logger, option.DNSOptions{})
	require.NoError(t, err)
	ctx = service.ContextWith[adapter.DNSRouter](ctx, router)
	predefined := new(badjson.TypedMap[string, badoption.Listable[netip.Addr]])
	predefined.Put(cloudflareAPIHost, addresses)
	require.NoError(t, dnsManager.Create(ctx, logger.Logger(), "bootstrap", C.DNSTypeHosts, &option.HostsDNSServerOptions{Predefined: predefined}))
	scope := adapter.NewScope(ctx, logger.Logger())
	defer scope.Close()
	for _, stage := range adapter.ListStartStages {
		require.NoError(t, scope.Start("dns-transport", dnsManager, stage))
		require.NoError(t, scope.Start("dns-router", router, stage))
	}
	options := cloudflareOptions()
	options.APIToken = token
	options.ZoneID = os.Getenv("SING_BOX_TEST_CF_ZONE")
	options.RecordID = os.Getenv("SING_BOX_TEST_CF_RECORD")
	options.RulesetID = os.Getenv("SING_BOX_TEST_CF_RULESET")
	options.RuleID = os.Getenv("SING_BOX_TEST_CF_RULE")
	options.DomainResolver.Strategy = option.DomainStrategy(C.DomainStrategyIPv4Only)
	m, err := NewManager(ctx, logger.Logger(), option.ServiceDiscoveryOptions{Servers: []option.ServiceDiscoveryServerOptions{options}})
	require.NoError(t, err)
	defer m.Close()
	p := m.providers["cf"].(*cloudflareProvider)
	counted := &countingHTTPTransport{Transport: p.client.Transport.(*http.Transport)}
	p.client.Transport = counted
	q := option.ServiceDiscoveryQueryOptions{Server: "cf"}
	first, err := m.Discover(ctx, q)
	require.NoError(t, err)
	require.Len(t, first, 1)
	require.True(t, first[0].IsValid())
	require.NotZero(t, first[0].Port())
	require.EqualValues(t, 2, counted.calls.Load())
	second, err := m.Discover(ctx, q)
	require.NoError(t, err)
	require.Equal(t, first, second)
	require.EqualValues(t, 2, counted.calls.Load())
	t.Logf("read-only discovery passed: IPv6=%t, valid IP:port, two API reads, repeat query cached", first[0].Addr().Is6())
}

type countingHTTPTransport struct {
	*http.Transport
	calls atomic.Int32
}

func (t *countingHTTPTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	t.calls.Add(1)
	return t.Transport.RoundTrip(r)
}
