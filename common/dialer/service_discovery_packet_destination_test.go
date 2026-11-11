package dialer

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"testing"
	"time"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/option"
	M "github.com/sagernet/sing/common/metadata"
	"github.com/sagernet/sing/service"

	"github.com/stretchr/testify/require"
)

func TestServiceDiscoveryPacketDestinationMapping(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	require.NoError(t, err)
	t.Cleanup(func() { listener.Close() })
	actual := M.SocksaddrFromNet(listener.LocalAddr())
	sd := &fakeServiceDiscovery{addresses: []netip.AddrPort{actual.AddrPort()}}
	ctx := service.ContextWith[adapter.ServiceDiscovery](t.Context(), sd)
	server := option.ServerOptions{Server: "unresolvable.invalid", ServerPort: 1}
	d, err := NewServer(ctx, nodeOptions(), server)
	require.NoError(t, err)
	packetDialer, ok := d.(PacketDialerWithDestination)
	require.True(t, ok)
	for _, destination := range []M.Socksaddr{server.Build(), actual} {
		conn, address, err := packetDialer.ListenPacketWithDestination(ctx, destination)
		require.NoError(t, err)
		t.Cleanup(func() { conn.Close() })
		require.False(t, address.IsValid(), "connection already accepts the logical destination")
		require.NoError(t, conn.SetDeadline(time.Now().Add(5*time.Second)))
		require.NoError(t, listener.SetDeadline(time.Now().Add(5*time.Second)))
		_, err = conn.WriteTo([]byte("ping"), destination)
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
		require.Equal(t, destination, M.SocksaddrFromNet(source))
		require.NoError(t, conn.Close())
	}
	require.EqualValues(t, 1, sd.calls.Load(), "auxiliary destination must bypass discovery")
	sd.err = errors.New("discovery unavailable")
	conn, address, err := packetDialer.ListenPacketWithDestination(ctx, server.Build())
	require.ErrorIs(t, err, sd.err)
	require.Nil(t, conn)
	require.False(t, address.IsValid())
}

type packetDestinationCompatibilityDialer struct {
	ctx              context.Context
	destination      M.Socksaddr
	packetCalls      int
	destinationCalls int
	err              error
}

func (d *packetDestinationCompatibilityDialer) DialContext(context.Context, string, M.Socksaddr) (net.Conn, error) {
	return nil, d.err
}

func (d *packetDestinationCompatibilityDialer) ListenPacket(ctx context.Context, destination M.Socksaddr) (net.PacketConn, error) {
	d.ctx, d.destination = ctx, destination
	d.packetCalls++
	return nil, d.err
}

func (d *packetDestinationCompatibilityDialer) ListenPacketWithDestination(context.Context, M.Socksaddr) (net.PacketConn, netip.Addr, error) {
	d.destinationCalls++
	return nil, netip.MustParseAddr("192.0.2.1"), d.err
}

func TestServiceDiscoveryPacketDestinationPreservesUnderlyingContract(t *testing.T) {
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	underlying := &packetDestinationCompatibilityDialer{err: context.Canceled}
	server := M.Socksaddr{Fqdn: "node.invalid", Port: 1}
	auxiliary := M.Socksaddr{Fqdn: "auxiliary.invalid", Port: 53}
	d := &serviceDiscoveryDialer{dialer: underlying, server: server}
	conn, address, err := d.ListenPacketWithDestination(ctx, auxiliary)
	require.ErrorIs(t, err, context.Canceled)
	require.Nil(t, conn)
	require.False(t, address.IsValid())
	require.Same(t, ctx, underlying.ctx)
	require.Equal(t, auxiliary, underlying.destination)
	require.Equal(t, 1, underlying.packetCalls)
	require.Zero(t, underlying.destinationCalls, "preserve ListenPacket mapping instead of exposing a bare resolved IP")
}
