package adapter

import (
	"context"
	"net/netip"

	"github.com/sagernet/sing-box/option"
)

type ServiceDiscoveryResult struct {
	Addresses []netip.AddrPort
	TTL       uint32
}

type ServiceDiscoveryProvider interface {
	Discover(context.Context) (ServiceDiscoveryResult, error)
	Close() error
}

type ServiceDiscovery interface {
	LifecycleService
	HasServer(string) bool
	Discover(context.Context, option.ServiceDiscoveryQueryOptions) ([]netip.AddrPort, error)
}
