package dialer

import (
	"context"
	"net"
	"strings"
	"time"

	"github.com/sagernet/sing-box/adapter"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/common/bufio"
	E "github.com/sagernet/sing/common/exceptions"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
)

var _ ParallelInterfaceResolveDialer = (*serviceDiscoveryDialer)(nil)

type serviceDiscoveryDialer struct {
	dialer  N.Dialer
	manager adapter.ServiceDiscovery
	server  M.Socksaddr
	options option.ServiceDiscoveryQueryOptions
}

func (d *serviceDiscoveryDialer) destinations(ctx context.Context, destination M.Socksaddr) ([]M.Socksaddr, error) {
	if destination.Addr != d.server.Addr || !strings.EqualFold(strings.TrimSuffix(destination.Fqdn, "."), strings.TrimSuffix(d.server.Fqdn, ".")) ||
		d.server.Port != 0 && destination.Port != d.server.Port {
		return []M.Socksaddr{destination}, nil
	}
	addresses, err := d.manager.Discover(ctx, d.options)
	if err != nil {
		return nil, err
	}
	if len(addresses) == 0 {
		return nil, E.New("sd returned no addresses")
	}
	destinations := make([]M.Socksaddr, 0, len(addresses))
	for _, address := range addresses {
		destinations = append(destinations, M.SocksaddrFrom(address.Addr(), address.Port()))
	}
	return destinations, nil
}

func (d *serviceDiscoveryDialer) DialContext(ctx context.Context, network string, destination M.Socksaddr) (net.Conn, error) {
	destinations, err := d.destinations(ctx, destination)
	if err != nil {
		return nil, err
	}
	var errs []error
	for _, actual := range destinations {
		conn, err := d.dialer.DialContext(ctx, network, actual)
		if err == nil {
			return conn, nil
		}
		errs = append(errs, err)
		if ctx.Err() != nil {
			break
		}
	}
	return nil, E.Errors(errs...)
}

func (d *serviceDiscoveryDialer) ListenPacket(ctx context.Context, destination M.Socksaddr) (net.PacketConn, error) {
	return d.listenPacket(ctx, destination, nil, nil, nil, 0, false)
}

func (d *serviceDiscoveryDialer) DialParallelInterface(ctx context.Context, network string, destination M.Socksaddr, strategy *C.NetworkStrategy, interfaceType, fallbackInterfaceType []C.InterfaceType, fallbackDelay time.Duration) (net.Conn, error) {
	parallel, ok := d.dialer.(ParallelInterfaceDialer)
	if !ok {
		return d.DialContext(ctx, network, destination)
	}
	destinations, err := d.destinations(ctx, destination)
	if err != nil {
		return nil, err
	}
	var errs []error
	for _, actual := range destinations {
		conn, err := parallel.DialParallelInterface(ctx, network, actual, strategy, interfaceType, fallbackInterfaceType, fallbackDelay)
		if err == nil {
			return conn, nil
		}
		errs = append(errs, err)
		if ctx.Err() != nil {
			break
		}
	}
	return nil, E.Errors(errs...)
}

func (d *serviceDiscoveryDialer) ListenSerialInterfacePacket(ctx context.Context, destination M.Socksaddr, strategy *C.NetworkStrategy, interfaceType, fallbackInterfaceType []C.InterfaceType, fallbackDelay time.Duration) (net.PacketConn, error) {
	return d.listenPacket(ctx, destination, strategy, interfaceType, fallbackInterfaceType, fallbackDelay, true)
}

func (d *serviceDiscoveryDialer) listenPacket(ctx context.Context, destination M.Socksaddr, strategy *C.NetworkStrategy, interfaceType, fallbackInterfaceType []C.InterfaceType, fallbackDelay time.Duration, useInterface bool) (net.PacketConn, error) {
	destinations, err := d.destinations(ctx, destination)
	if err != nil {
		return nil, err
	}
	var errs []error
	for _, actual := range destinations {
		var conn net.PacketConn
		if parallel, ok := d.dialer.(ParallelInterfaceDialer); ok && useInterface {
			conn, err = parallel.ListenSerialInterfacePacket(ctx, actual, strategy, interfaceType, fallbackInterfaceType, fallbackDelay)
		} else {
			conn, err = d.dialer.ListenPacket(ctx, actual)
		}
		if err == nil {
			if actual == destination {
				return conn, nil
			}
			return &serviceDiscoveryPacketConn{
				NATPacketConn: bufio.NewDestinationNATPacketConn(bufio.NewPacketConn(conn), actual, destination),
				raw:           conn, actual: actual, original: destination,
			}, nil
		}
		errs = append(errs, err)
		if ctx.Err() != nil {
			break
		}
	}
	return nil, E.Errors(errs...)
}

func (d *serviceDiscoveryDialer) QueryOptions() adapter.DNSQueryOptions {
	if resolver, ok := d.dialer.(ResolveDialer); ok {
		return resolver.QueryOptions()
	}
	return adapter.DNSQueryOptions{}
}

func (d *serviceDiscoveryDialer) Upstream() any { return d.dialer }

// Unlike IP-only DNS NAT, SD translates the entire endpoint. Preserve a logical
// domain address on the net.PacketConn API as well as on the packet/batch APIs.
type serviceDiscoveryPacketConn struct {
	bufio.NATPacketConn
	raw      net.PacketConn
	actual   M.Socksaddr
	original M.Socksaddr
}

func (c *serviceDiscoveryPacketConn) ReadFrom(p []byte) (int, net.Addr, error) {
	n, address, err := c.raw.ReadFrom(p)
	if err == nil && M.SocksaddrFromNet(address) == c.actual {
		address = c.original
	}
	return n, address, err
}

func (c *serviceDiscoveryPacketConn) RemoteAddr() net.Addr { return c.original }
