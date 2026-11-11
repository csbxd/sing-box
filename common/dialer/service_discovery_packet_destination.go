package dialer

import (
	"context"
	"net"
	"net/netip"

	M "github.com/sagernet/sing/common/metadata"
)

// ListenPacketWithDestination preserves the full endpoint mapping performed by
// ListenPacket. An invalid address tells callers that the returned connection
// already accepts the logical destination; an IP alone cannot represent SD's
// translated port.
func (d *serviceDiscoveryDialer) ListenPacketWithDestination(ctx context.Context, destination M.Socksaddr) (net.PacketConn, netip.Addr, error) {
	conn, err := d.ListenPacket(ctx, destination)
	return conn, netip.Addr{}, err
}
