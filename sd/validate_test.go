package sd

import (
	"testing"

	"github.com/sagernet/sing-box/option"

	"github.com/stretchr/testify/require"
)

func TestDiscoveryDependencies(t *testing.T) {
	for _, name := range []string{"valid", "missing bootstrap", "missing detour", "circular detour", "circular bootstrap", "duplicate tag"} {
		t.Run(name, func(t *testing.T) {
			cf := cloudflareOptions()
			bootstrap := &option.RemoteDNSServerOptions{DNSServerAddressOptions: option.DNSServerAddressOptions{Server: "1.1.1.1"}}
			node := &option.TrojanOutboundOptions{DialerOptions: option.DialerOptions{AbstractDialerOptions: option.AbstractDialerOptions{ServiceDiscovery: &option.ServiceDiscoveryQueryOptions{Server: "cf"}}}}
			config := option.Options{
				ServiceDiscovery: &option.ServiceDiscoveryOptions{Servers: []option.ServiceDiscoveryServerOptions{cf}},
				DNS:              &option.DNSOptions{RawDNSOptions: option.RawDNSOptions{Servers: []option.DNSServerOptions{{Type: "udp", Tag: "bootstrap", Options: bootstrap}}}},
				Outbounds:        []option.Outbound{{Type: "trojan", Tag: "node", Options: node}},
			}
			switch name {
			case "missing bootstrap":
				config.DNS = nil
			case "missing detour":
				config.ServiceDiscovery.Servers[0].Detour = "missing"
			case "circular detour":
				config.ServiceDiscovery.Servers[0].Detour = "node"
			case "circular bootstrap":
				bootstrap.Detour = "node"
			case "duplicate tag":
				config.ServiceDiscovery.Servers = append(config.ServiceDiscovery.Servers, cf)
			}
			err := ValidateOptions(config)
			if name == "valid" {
				require.NoError(t, err)
			} else {
				require.Error(t, err)
			}
		})
	}
}
