package dialer

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"sync/atomic"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/option"
	M "github.com/sagernet/sing/common/metadata"
	"github.com/sagernet/sing/service"

	"github.com/stretchr/testify/require"
)

type fakeServiceDiscovery struct {
	adapter.ServiceDiscovery
	addresses []netip.AddrPort
	err       error
	calls     atomic.Int32
}

func (s *fakeServiceDiscovery) HasServer(tag string) bool { return tag == "cf" }
func (s *fakeServiceDiscovery) Discover(context.Context, option.ServiceDiscoveryQueryOptions) ([]netip.AddrPort, error) {
	s.calls.Add(1)
	return s.addresses, s.err
}

func nodeOptions() option.DialerOptions {
	return option.DialerOptions{AbstractDialerOptions: option.AbstractDialerOptions{ServiceDiscovery: &option.ServiceDiscoveryQueryOptions{Server: "cf"}}}
}

func TestServiceDiscoveryTCP(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	defer listener.Close()
	sd := &fakeServiceDiscovery{addresses: []netip.AddrPort{M.SocksaddrFromNet(listener.Addr()).AddrPort()}}
	ctx := service.ContextWith[adapter.ServiceDiscovery](t.Context(), sd)
	server := option.ServerOptions{Server: "unresolvable.invalid", ServerPort: 1}
	d, err := NewServer(ctx, nodeOptions(), server)
	require.NoError(t, err)
	for _, parallel := range []bool{false, true} {
		var conn net.Conn
		if parallel {
			conn, err = d.(ParallelInterfaceDialer).DialParallelInterface(ctx, "tcp", server.Build(), nil, nil, nil, 0)
		} else {
			conn, err = d.DialContext(ctx, "tcp", server.Build())
		}
		require.NoError(t, err)
		require.Equal(t, listener.Addr().String(), conn.RemoteAddr().String())
		require.NoError(t, conn.Close())
	}
	// A different destination on the same dialer is an auxiliary connection.
	conn, err := d.DialContext(ctx, "tcp", M.SocksaddrFromNet(listener.Addr()))
	require.NoError(t, err)
	conn.Close()
	require.EqualValues(t, 2, sd.calls.Load())
	sd.err = errors.New("discovery unavailable")
	_, err = d.DialContext(ctx, "tcp", server.Build())
	require.ErrorContains(t, err, "discovery unavailable")
}

func TestServiceDiscoveryUDP(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	require.NoError(t, err)
	defer listener.Close()
	sd := &fakeServiceDiscovery{addresses: []netip.AddrPort{M.SocksaddrFromNet(listener.LocalAddr()).AddrPort()}}
	ctx := service.ContextWith[adapter.ServiceDiscovery](t.Context(), sd)
	server := option.ServerOptions{Server: "unresolvable.invalid", ServerPort: 1}
	d, err := NewServer(ctx, nodeOptions(), server)
	require.NoError(t, err)
	// Connected UDP is the path used by QUIC clients such as TUIC and Hysteria2.
	connected, err := d.DialContext(ctx, "udp", server.Build())
	require.NoError(t, err)
	require.Equal(t, listener.LocalAddr().String(), connected.RemoteAddr().String())
	connected.Close()
	for _, parallel := range []bool{false, true} {
		var conn net.PacketConn
		if parallel {
			conn, err = d.(ParallelInterfaceDialer).ListenSerialInterfacePacket(ctx, server.Build(), nil, nil, nil, 0)
		} else {
			conn, err = d.ListenPacket(ctx, server.Build())
		}
		require.NoError(t, err)
		require.NoError(t, conn.SetDeadline(time.Now().Add(time.Second)))
		require.NoError(t, listener.SetDeadline(time.Now().Add(time.Second)))
		_, err = conn.WriteTo([]byte("ping"), server.Build())
		require.NoError(t, err)
		buffer := make([]byte, 64)
		n, source, err := listener.ReadFrom(buffer)
		require.NoError(t, err)
		require.Equal(t, "ping", string(buffer[:n]))
		_, err = listener.WriteTo([]byte("pong"), source)
		require.NoError(t, err)
		n, source, err = conn.ReadFrom(buffer)
		require.NoError(t, err)
		require.Equal(t, "pong", string(buffer[:n]))
		require.Equal(t, server.Build(), M.SocksaddrFromNet(source))
		require.NoError(t, conn.Close())
	}
}

func TestServiceDiscoveryDisabledAndInvalid(t *testing.T) {
	server := option.ServerOptions{Server: "127.0.0.1", ServerPort: 1}
	d, err := NewServer(t.Context(), option.DialerOptions{}, server)
	require.NoError(t, err)
	_, wrapped := d.(*serviceDiscoveryDialer)
	require.False(t, wrapped)
	_, err = NewServer(t.Context(), nodeOptions(), server)
	require.ErrorContains(t, err, "sd server not found")
	_, err = New(t.Context(), nodeOptions(), false)
	require.ErrorContains(t, err, "supported node dialer")
}
