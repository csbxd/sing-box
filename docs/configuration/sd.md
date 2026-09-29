# Service Discovery

`sd` is an optional, independent service discovery subsystem. A provider returns
complete IP:port endpoints. A node outbound that explicitly selects `sd` uses
these endpoints instead of its configured dial address and port. DNS Lookup and
Exchange, DNS rules, and DNS caches are unchanged.

### Example

```json
{
  "dns": {
    "servers": [
      { "type": "udp", "tag": "bootstrap", "server": "1.1.1.1" }
    ]
  },
  "sd": {
    "servers": [
      {
        "type": "cloudflare",
        "tag": "cf-origin",
        "api_token": "<TOKEN>",
        "zone_id": "<ZONE_ID>",
        "record_id": "<RECORD_ID>",
        "ruleset_id": "<RULESET_ID>",
        "rule_id": "<RULE_ID>",
        "domain_resolver": "bootstrap"
      }
    ],
    "timeout": "10s",
    "strategy": "prefer_ipv4",
    "disable_cache": false,
    "disable_expire": false,
    "cache_capacity": 1024,
    "optimistic": false
  },
  "outbounds": [
    {
      "type": "trojan",
      "tag": "node",
      "server": "node.example.com",
      "server_port": 443,
      "password": "<PASSWORD>",
      "tls": { "enabled": true, "server_name": "node.example.com" },
      "sd": { "server": "cf-origin", "rewrite_ttl": 60 }
    }
  ]
}
```

### Global options

| Field | Meaning |
| --- | --- |
| `servers` | Discovery providers with unique, nonempty tags. |
| `timeout` | Query timeout, default 10 seconds, as for DNS. Covers the entire provider lookup. |
| `strategy` | `prefer_ipv4`, `prefer_ipv6`, `ipv4_only`, or `ipv6_only`. Default preserves provider order. |
| `disable_cache` | Disable cache reads and writes. Default false. |
| `disable_expire` | Ignore cache TTL expiry. Entries can still be evicted. Default false. |
| `cache_capacity` | Maximum cached results; values below 1024 use 1024, matching DNS. |
| `optimistic` | Allow stale entries while refreshing in the background. Default false. Accepts a boolean or `{ "enabled": true, "timeout": "5m" }`; timeout is the allowed period after expiry, default 3 days. Conflicts with `disable_cache` and `disable_expire`. |

The cache is independent, in memory, keyed by provider tag, and stores complete
IP:port results. A refresh replaces the entry only when the entire discovery
succeeds. Concurrent cache misses with the same tag and query timeout are merged.
No DNS rules, DNS fake IPs, DNS persistent storage, or ECS are used for discovery.

### Per-node options

The presence of `sd` enables discovery; omit it to retain the existing behavior.
`"sd": "cf-origin"` is shorthand for `{ "server": "cf-origin" }`.

| Field | Meaning |
| --- | --- |
| `server` | Required discovery provider tag. |
| `timeout` | Override the global query timeout. |
| `strategy` | Override the global address-family strategy. |
| `disable_cache` | Disable caching for this query. A false value does not override a global true value, matching DNS. |
| `disable_optimistic_cache` | Require a fresh result for this query. |
| `rewrite_ttl` | Override the result lifetime in seconds; zero prevents storage of newly fetched results. Like DNS, this affects newly stored entries, not an existing shared cache entry. |

Each new node connection or UDP PacketConn resolves an endpoint. Existing
connections keep their endpoint, even after cache expiry. Discovery errors do not
fall back to `server` or `server_port`. The configured logical node identity and
TLS configuration are retained. Auxiliary destinations on the dialer are not rewritten.

The common node dialer supports HTTP, SOCKS, VMess, VLESS, Trojan, Shadowsocks,
ShadowTLS, Snell, SSH, AnyTLS, TUIC, Hysteria, Hysteria2 and MASQUE client endpoints.
TCP, connected UDP, UDP PacketConn and interface-aware dialing are supported.
`server_ports` and Hysteria2 `realm` cannot be combined with `sd`. Other dialers
without a bound node address reject `sd` rather than silently ignoring it.

### Cloudflare provider

`type: "cloudflare"` requires `api_token`, `zone_id`, `record_id`, `ruleset_id`,
`rule_id`, and an independent `domain_resolver` for the API bootstrap. Standard
dial fields such as `detour`, interface binding and connect timeout apply to API
requests. Bootstrap DNS and detours must not depend on the same SD provider.

This reads the data published by [natpunch/natcf](https://github.com/csbxd/natpunch/tree/main/natcf):

* IP: `GET /zones/{zone_id}/dns_records/{record_id}`, A or AAAA `content`, including proxied records.
* Port: `GET /zones/{zone_id}/rulesets/{ruleset_id}`, selecting `rule_id` and reading `action_parameters.origin.port`.

The token needs DNS record read access and permission to read the origin ruleset.
Only an enabled `route` rule in the `http_request_origin` phase with port 1–65535
is accepted. CNAME records and origin host overrides are not supported. The API
endpoint is fixed to `https://api.cloudflare.com/client/v4`, with no redirects.
No records or rules are written.

The record's TTL supplies the endpoint lifetime; Cloudflare automatic TTL (`1`)
maps to the project default of 600 seconds. Use `rewrite_ttl` for faster refreshes.
The provider reads both resources on each refresh, but Cloudflare updates to the
record and rule are not atomic: discovery cannot guarantee a server-side snapshot.
An Origin Rule's TCP port does not establish UDP reachability; the published
endpoint must actually serve the node's transport protocol.
