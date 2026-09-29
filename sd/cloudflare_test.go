package sd

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
	"time"

	C "github.com/sagernet/sing-box/constant"
	"github.com/sagernet/sing-box/option"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func cloudflareOptions() option.ServiceDiscoveryServerOptions {
	return option.ServiceDiscoveryServerOptions{
		Type: "cloudflare", Tag: "cf", APIToken: "test-secret", ZoneID: "zone", RecordID: "record", RulesetID: "ruleset", RuleID: "rule",
		DialerOptions: option.DialerOptions{AbstractDialerOptions: option.AbstractDialerOptions{DomainResolver: &option.DomainResolveOptions{Server: "bootstrap"}}},
	}
}

func testCloudflare(t *testing.T, handler http.HandlerFunc) *cloudflareProvider {
	t.Helper()
	p, err := newCloudflare(t.Context(), cloudflareOptions())
	require.NoError(t, err)
	s := httptest.NewServer(handler)
	t.Cleanup(s.Close)
	p.client.CloseIdleConnections()
	p.client.Transport = s.Client().Transport
	p.baseURL = s.URL
	t.Cleanup(func() { require.NoError(t, p.Close()) })
	return p
}

func recordResult(address string, ttl uint32) map[string]any {
	typ := "A"
	if strings.Contains(address, ":") {
		typ = "AAAA"
	}
	return map[string]any{"id": "record", "type": typ, "content": address, "ttl": ttl}
}

func ruleResult(port int) map[string]any {
	return map[string]any{
		"id": "rule", "enabled": true, "action": "route",
		"action_parameters": map[string]any{"origin": map[string]any{"port": port}},
	}
}

func apiHandler(t *testing.T, record, rule map[string]any) http.HandlerFunc {
	t.Helper()
	return func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodGet, r.Method)
		assert.Equal(t, "Bearer test-secret", r.Header.Get("Authorization"))
		var result any
		switch r.URL.Path {
		case "/zones/zone/dns_records/record":
			result = record
		case "/zones/zone/rulesets/ruleset":
			result = map[string]any{"id": "ruleset", "phase": "http_request_origin", "rules": []any{rule}}
		default:
			t.Errorf("unexpected request path: %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		assert.NoError(t, json.NewEncoder(w).Encode(map[string]any{"success": true, "result": result}))
	}
}

func TestCloudflareDiscover(t *testing.T) {
	for _, tc := range []struct {
		name, address    string
		port             int
		ttl, expectedTTL uint32
	}{
		{"IPv4", "192.0.2.1", 26656, 60, 60},
		{"IPv6", "2001:db8::1", 65535, 120, 120},
		{"automatic TTL", "192.0.2.1", 1, 1, C.DefaultDNSTTL},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := testCloudflare(t, apiHandler(t, recordResult(tc.address, tc.ttl), ruleResult(tc.port)))
			result, err := p.Discover(t.Context())
			require.NoError(t, err)
			require.Equal(t, []netip.AddrPort{netip.AddrPortFrom(netip.MustParseAddr(tc.address), uint16(tc.port))}, result.Addresses)
			require.Equal(t, tc.expectedTTL, result.TTL)
		})
	}
}

func TestCloudflareInvalidResults(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(map[string]any, map[string]any)
	}{
		{"wrong record ID", func(r, _ map[string]any) { r["id"] = "other" }},
		{"CNAME", func(r, _ map[string]any) { r["type"] = "CNAME" }},
		{"invalid address", func(r, _ map[string]any) { r["content"] = "test-secret" }},
		{"wrong address family", func(r, _ map[string]any) { r["type"] = "AAAA" }},
		{"zero TTL", func(r, _ map[string]any) { r["ttl"] = 0 }},
		{"missing rule", func(_, r map[string]any) { r["id"] = "other" }},
		{"disabled rule", func(_, r map[string]any) { r["enabled"] = false }},
		{"wrong action", func(_, r map[string]any) { r["action"] = "block" }},
		{"missing port", func(_, r map[string]any) { r["action_parameters"] = nil }},
		{"port overflow", func(_, r map[string]any) { r["action_parameters"] = ruleResult(65536)["action_parameters"] }},
		{"negative port", func(_, r map[string]any) { r["action_parameters"] = ruleResult(-1)["action_parameters"] }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			record, rule := recordResult("192.0.2.1", 60), ruleResult(443)
			tc.mutate(record, rule)
			p := testCloudflare(t, apiHandler(t, record, rule))
			result, err := p.Discover(t.Context())
			require.Error(t, err)
			require.Empty(t, result.Addresses)
			require.NotContains(t, err.Error(), "test-secret")
		})
	}
}

func TestCloudflareAPIErrors(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
	}{
		{"unauthorized", 401, "test-secret"},
		{"forbidden", 403, "test-secret"},
		{"missing", 404, "test-secret"},
		{"rate limit", 429, "test-secret"},
		{"server error", 500, "test-secret"},
		{"invalid JSON", 200, "test-secret"},
		{"null result", 200, `{"success":true,"result":null}`},
		{"unsuccessful", 200, `{"success":false,"errors":[{"message":"test-secret"}]}`},
		{"too large", 200, strings.Repeat("x", maxResponseSize+1)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := testCloudflare(t, func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			})
			_, err := p.Discover(t.Context())
			require.Error(t, err)
			require.NotContains(t, err.Error(), "test-secret")
		})
	}
	t.Run("timeout", func(t *testing.T) {
		p := testCloudflare(t, func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() })
		ctx, cancel := context.WithTimeout(t.Context(), 30*time.Millisecond)
		defer cancel()
		_, err := p.Discover(ctx)
		require.ErrorIs(t, err, context.DeadlineExceeded)
	})
	t.Run("redirect", func(t *testing.T) {
		p := testCloudflare(t, func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, "/other", http.StatusFound) })
		_, err := p.Discover(t.Context())
		require.ErrorContains(t, err, "HTTP 302")
	})
	t.Run("partial failure", func(t *testing.T) {
		handler := apiHandler(t, recordResult("192.0.2.1", 60), ruleResult(443))
		p := testCloudflare(t, func(w http.ResponseWriter, r *http.Request) {
			if strings.Contains(r.URL.Path, "rulesets") {
				w.WriteHeader(503)
				return
			}
			handler(w, r)
		})
		result, err := p.Discover(t.Context())
		require.Error(t, err)
		require.Empty(t, result.Addresses)
	})
}
