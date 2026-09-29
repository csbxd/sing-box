package box_test

import (
	"context"
	"testing"

	box "github.com/sagernet/sing-box"
	"github.com/sagernet/sing-box/include"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/common/json"

	"github.com/stretchr/testify/require"
)

func TestServiceDiscoveryConfiguration(t *testing.T) {
	ctx := include.Context(context.Background())
	var options option.Options
	require.NoError(t, json.UnmarshalContext(ctx, []byte(`{
 "dns":{"servers":[{"type":"udp","tag":"bootstrap","server":"1.1.1.1"}]},
 "sd":{"servers":[{"type":"cloudflare","tag":"cf","api_token":"placeholder","zone_id":"zone","record_id":"record","ruleset_id":"ruleset","rule_id":"rule","domain_resolver":"bootstrap"}],"timeout":"10s","optimistic":{"enabled":true,"timeout":"5m"}},
 "outbounds":[{"type":"trojan","tag":"node","server":"node.invalid","server_port":443,"password":"placeholder","tls":{"enabled":true},"sd":{"server":"cf","rewrite_ttl":60}}]
}`), &options))
	instance, err := box.New(box.Options{Context: ctx, Options: options})
	require.NoError(t, err)
	require.NoError(t, instance.Close())
}
