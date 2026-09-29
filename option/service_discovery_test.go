package option

import (
	"testing"

	"github.com/sagernet/sing/common/json"

	"github.com/stretchr/testify/require"
)

func TestServiceDiscoveryQueryOptions(t *testing.T) {
	for _, input := range []string{`"cf"`, `{"server":"cf","timeout":"5s","strategy":"prefer_ipv4","disable_cache":true,"disable_optimistic_cache":true,"rewrite_ttl":60}`} {
		var options ServiceDiscoveryQueryOptions
		require.NoError(t, json.Unmarshal([]byte(input), &options))
		encoded, err := json.Marshal(options)
		require.NoError(t, err)
		var restored ServiceDiscoveryQueryOptions
		require.NoError(t, json.Unmarshal(encoded, &restored))
		require.Equal(t, options, restored)
	}
	for _, input := range []string{`""`, `{}`, `{"server":"cf","client_subnet":"192.0.2.0/24"}`} {
		var options ServiceDiscoveryQueryOptions
		require.Error(t, json.Unmarshal([]byte(input), &options))
	}
}
