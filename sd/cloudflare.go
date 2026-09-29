package sd

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strings"

	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/common/dialer"
	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"
	E "github.com/sagernet/sing/common/exceptions"
	M "github.com/sagernet/sing/common/metadata"
)

const (
	cloudflareAPIHost = "api.cloudflare.com"
	maxResponseSize   = 1024 * 1024
)

type cloudflareProvider struct {
	client  *http.Client
	baseURL string
	options option.ServiceDiscoveryServerOptions
}

func newCloudflare(ctx context.Context, options option.ServiceDiscoveryServerOptions) (*cloudflareProvider, error) {
	if strings.TrimSpace(options.APIToken) == "" || strings.ContainsAny(options.APIToken, "\r\n") {
		return nil, E.New("missing or invalid api_token")
	}
	for _, value := range []string{options.ZoneID, options.RecordID, options.RulesetID, options.RuleID} {
		if strings.TrimSpace(value) == "" {
			return nil, E.New("zone_id, record_id, ruleset_id and rule_id are required")
		}
	}
	if options.ServiceDiscovery != nil {
		return nil, E.New("sd cannot be used for a service discovery API connection")
	}
	if options.DomainResolver == nil || options.DomainResolver.Server == "" {
		return nil, E.New("domain_resolver is required for API bootstrap")
	}
	apiDialer, err := dialer.NewWithOptions(dialer.Options{
		Context: ctx, Options: options.DialerOptions, RemoteIsDomain: true, DirectResolver: true,
	})
	if err != nil {
		return nil, err
	}
	return &cloudflareProvider{
		options: options,
		baseURL: "https://" + cloudflareAPIHost + "/client/v4",
		client: &http.Client{
			Transport: &http.Transport{
				DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
					return apiDialer.DialContext(ctx, network, M.ParseSocksaddr(address))
				},
				ForceAttemptHTTP2: true, MaxIdleConns: 2, IdleConnTimeout: C.DNSTimeout, TLSHandshakeTimeout: C.DNSTimeout,
			},
			CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
		},
	}, nil
}

func (p *cloudflareProvider) Close() error { p.client.CloseIdleConnections(); return nil }

func (p *cloudflareProvider) Discover(ctx context.Context) (adapter.ServiceDiscoveryResult, error) {
	var record struct {
		ID      string `json:"id"`
		Type    string `json:"type"`
		Content string `json:"content"`
		TTL     uint32 `json:"ttl"`
	}
	zonePath := "/zones/" + url.PathEscape(p.options.ZoneID)
	err := p.get(ctx, zonePath+"/dns_records/"+url.PathEscape(p.options.RecordID), &record)
	if err != nil {
		return adapter.ServiceDiscoveryResult{}, err
	}
	address, err := netip.ParseAddr(record.Content)
	if err != nil || record.ID != p.options.RecordID || address.Zone() != "" ||
		!(record.Type == "A" && address.Is4() || record.Type == "AAAA" && address.Is6() && !address.Is4In6()) {
		return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: invalid A/AAAA record or record ID")
	}
	if record.TTL == 0 {
		return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: invalid record TTL")
	}
	if record.TTL == 1 {
		record.TTL = C.DefaultDNSTTL
	}
	var ruleset struct {
		ID    string `json:"id"`
		Phase string `json:"phase"`
		Rules []struct {
			ID         string `json:"id"`
			Action     string `json:"action"`
			Enabled    *bool  `json:"enabled"`
			Parameters struct {
				Origin struct {
					Port int    `json:"port"`
					Host string `json:"host"`
				} `json:"origin"`
			} `json:"action_parameters"`
		} `json:"rules"`
	}
	err = p.get(ctx, zonePath+"/rulesets/"+url.PathEscape(p.options.RulesetID), &ruleset)
	if err != nil {
		return adapter.ServiceDiscoveryResult{}, err
	}
	if ruleset.ID != p.options.RulesetID || ruleset.Phase != "http_request_origin" {
		return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: invalid origin ruleset")
	}
	for _, rule := range ruleset.Rules {
		if rule.ID != p.options.RuleID {
			continue
		}
		if rule.Enabled != nil && !*rule.Enabled || rule.Action != "route" {
			return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: origin rule is disabled or is not a route action")
		}
		origin := rule.Parameters.Origin
		if origin.Port < 1 || origin.Port > 65535 {
			return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: missing or invalid origin port")
		}
		if origin.Host != "" {
			return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: origin host override is not supported")
		}
		return adapter.ServiceDiscoveryResult{Addresses: []netip.AddrPort{netip.AddrPortFrom(address, uint16(origin.Port))}, TTL: record.TTL}, nil
	}
	return adapter.ServiceDiscoveryResult{}, E.New("cloudflare: origin rule not found")
}

func (p *cloudflareProvider) get(ctx context.Context, path string, target any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.baseURL+path, nil)
	if err != nil {
		return E.New("cloudflare: invalid API request")
	}
	req.Header.Set("Authorization", "Bearer "+p.options.APIToken)
	response, err := p.client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return E.New("cloudflare: API request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return E.New("cloudflare: API returned HTTP ", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, maxResponseSize+1))
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return E.New("cloudflare: failed to read API response")
	}
	if len(body) > maxResponseSize {
		return E.New("cloudflare: API response exceeds size limit")
	}
	var envelope struct {
		Success bool            `json:"success"`
		Result  json.RawMessage `json:"result"`
	}
	if json.Unmarshal(body, &envelope) != nil || !envelope.Success || len(envelope.Result) == 0 || string(envelope.Result) == "null" {
		return E.New("cloudflare: unsuccessful or invalid API response")
	}
	// Never include API response bodies or transport errors: they may contain credentials.
	if json.Unmarshal(envelope.Result, target) != nil {
		return E.New("cloudflare: invalid API result")
	}
	return nil
}
