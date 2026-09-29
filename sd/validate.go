package sd

import (
	"strconv"
	"strings"

	"github.com/sagernet/sing-box/option"
	E "github.com/sagernet/sing/common/exceptions"
)

// ValidateOptions checks discovery/bootstrap dependencies before any network request.
func ValidateOptions(options option.Options) error {
	if options.ServiceDiscovery == nil {
		return nil
	}
	graph := make(map[string][]string)
	var roots []string
	addDialer := func(node string, dialer option.DialerOptions) {
		if dialer.Detour != "" {
			graph[node] = append(graph[node], "outbound/"+dialer.Detour)
		}
		if dialer.ServiceDiscovery != nil {
			graph[node] = append(graph[node], "sd/"+dialer.ServiceDiscovery.Server)
		}
		if dialer.DomainResolver != nil && dialer.DomainResolver.Server != "" {
			graph[node] = append(graph[node], "dns/"+dialer.DomainResolver.Server)
		}
	}
	addOptions := func(node string, raw any) {
		graph[node] = nil
		if wrapper, ok := raw.(option.DialerOptionsWrapper); ok {
			dialOptions := wrapper.TakeDialerOptions()
			addDialer(node, dialOptions)
			if server, ok := raw.(option.ServerOptionsWrapper); ok && strings.HasPrefix(node, "outbound/") && server.TakeServerOptions().ServerIsDomain() && dialOptions.ServiceDiscovery == nil && dialOptions.DomainResolver == nil && options.Route != nil && options.Route.DefaultDomainResolver != nil {
				graph[node] = append(graph[node], "dns/"+options.Route.DefaultDomainResolver.Server)
			}
		}
	}
	tagOrIndex := func(tag string, index int) string {
		if tag != "" {
			return tag
		}
		return strconv.Itoa(index)
	}
	for i, outbound := range options.Outbounds {
		node := "outbound/" + tagOrIndex(outbound.Tag, i)
		addOptions(node, outbound.Options)
		var members []string
		switch group := outbound.Options.(type) {
		case *option.SelectorOutboundOptions:
			members = group.Outbounds
		case *option.URLTestOutboundOptions:
			members = group.Outbounds
		}
		for _, tag := range members {
			graph[node] = append(graph[node], "outbound/"+tag)
		}
	}
	for i, endpoint := range options.Endpoints {
		addOptions("outbound/"+tagOrIndex(endpoint.Tag, i), endpoint.Options)
	}
	if options.DNS != nil {
		for i, server := range options.DNS.Servers {
			addOptions("dns/"+tagOrIndex(server.Tag, i), server.Options)
		}
	}
	for _, server := range options.ServiceDiscovery.Servers {
		node := "sd/" + server.Tag
		if _, exists := graph[node]; exists || server.Tag == "" {
			return E.New("sd: empty or duplicate tag: ", server.Tag)
		}
		graph[node] = nil
		addDialer(node, server.DialerOptions)
		roots = append(roots, node)
	}
	state := make(map[string]uint8)
	var visit func(string, []string) error
	visit = func(node string, path []string) error {
		if state[node] == 1 {
			return E.New("circular sd dependency: ", strings.Join(append(path, node), " -> "))
		}
		if state[node] == 2 {
			return nil
		}
		edges, exists := graph[node]
		if !exists {
			return E.New("sd dependency not found: ", node)
		}
		state[node] = 1
		for _, edge := range edges {
			if err := visit(edge, append(path, node)); err != nil {
				return err
			}
		}
		state[node] = 2
		return nil
	}
	for _, root := range roots {
		if err := visit(root, nil); err != nil {
			return err
		}
	}
	return nil
}
