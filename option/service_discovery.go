package option

import (
	"reflect"

	"github.com/sagernet/sing-box/schema"
	E "github.com/sagernet/sing/common/exceptions"
	"github.com/sagernet/sing/common/json"
	"github.com/sagernet/sing/common/json/badoption"
)

type ServiceDiscoveryOptions struct {
	Servers       []ServiceDiscoveryServerOptions `json:"servers,omitempty"`
	Strategy      DomainStrategy                  `json:"strategy,omitempty"`
	Timeout       badoption.Duration              `json:"timeout,omitempty"`
	DisableCache  bool                            `json:"disable_cache,omitempty"`
	DisableExpire bool                            `json:"disable_expire,omitempty"`
	CacheCapacity uint32                          `json:"cache_capacity,omitempty"`
	Optimistic    *OptimisticDNSOptions           `json:"optimistic,omitempty"`
}

type ServiceDiscoveryServerOptions struct {
	Type string `json:"type" enum:"cloudflare"`
	Tag  string `json:"tag"`
	DialerOptions
	APIToken  string `json:"api_token"`
	ZoneID    string `json:"zone_id"`
	RecordID  string `json:"record_id"`
	RulesetID string `json:"ruleset_id"`
	RuleID    string `json:"rule_id"`
}

type _ServiceDiscoveryQueryOptions struct {
	Server                 string             `json:"server" reference:"sd_server"`
	Timeout                badoption.Duration `json:"timeout,omitempty"`
	Strategy               DomainStrategy     `json:"strategy,omitempty"`
	DisableCache           bool               `json:"disable_cache,omitempty"`
	DisableOptimisticCache bool               `json:"disable_optimistic_cache,omitempty"`
	RewriteTTL             *uint32            `json:"rewrite_ttl,omitempty"`
}

type ServiceDiscoveryQueryOptions _ServiceDiscoveryQueryOptions

func (o ServiceDiscoveryQueryOptions) MarshalJSON() ([]byte, error) {
	if o.Timeout == 0 && o.Strategy == 0 && !o.DisableCache && !o.DisableOptimisticCache && o.RewriteTTL == nil {
		return json.Marshal(o.Server)
	}
	return json.Marshal(_ServiceDiscoveryQueryOptions(o))
}

func (o *ServiceDiscoveryQueryOptions) UnmarshalJSON(data []byte) error {
	var value string
	if json.Unmarshal(data, &value) == nil {
		*o = ServiceDiscoveryQueryOptions{Server: value}
	} else if err := json.UnmarshalDisallowUnknownFields(data, (*_ServiceDiscoveryQueryOptions)(o)); err != nil {
		return err
	}
	if o.Server == "" {
		return E.New("empty sd.server")
	}
	return nil
}

func (o ServiceDiscoveryQueryOptions) DescribeSchema(builder schema.Builder) (*schema.Node, error) {
	return builder.Define("ServiceDiscoveryQuery", func() (*schema.Node, error) {
		object := schema.StrictObject()
		if err := builder.FlattenStruct(object, reflect.TypeFor[_ServiceDiscoveryQueryOptions]()); err != nil {
			return nil, err
		}
		object.Required = []string{"server"}
		return schema.AnyOf(schema.TagReferenceNode("sd_server"), object), nil
	})
}
