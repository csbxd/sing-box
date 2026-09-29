# 服务发现

`sd` 是独立、按需启用的服务发现模块。Provider 返回完整的 IP:port；出站显式配置 `sd` 后，
公共节点拨号层使用发现结果覆盖实际连接的 `server` 和 `server_port`。
现有 DNS 查询、规则、缓存均保持不变。

### 配置示例

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

### 全局配置

| 字段 | 说明 |
| --- | --- |
| `servers` | 服务发现 Provider 列表，tag 必须非空且唯一。 |
| `timeout` | 一次完整发现的超时，默认 10 秒，与 DNS 一致。 |
| `strategy` | `prefer_ipv4`、`prefer_ipv6`、`ipv4_only` 或 `ipv6_only`；默认保留 Provider 的地址顺序。 |
| `disable_cache` | 禁用缓存读取和写入，默认 false。 |
| `disable_expire` | 忽略 TTL 过期，默认 false；条目仍可能被淘汰。 |
| `cache_capacity` | 缓存容量；小于 1024 时使用 1024，与 DNS 一致。 |
| `optimistic` | 允许使用过期缓存并后台刷新，默认 false。支持布尔值或 `{ "enabled": true, "timeout": "5m" }`；timeout 是过期后允许使用的时长，默认 3 天。与 `disable_cache`、`disable_expire` 冲突。 |

缓存独立保存在内存中，按 Provider tag 存储完整 IP:port 结果；刷新全部成功后才替换旧条目。
相同 tag 和查询超时的并发缓存未命中请求会合并。不使用 DNS 规则、FakeIP、DNS 持久化缓存或 ECS。

### 出站查询配置

配置 `sd` 即开启，省略则保持现有行为。
`"sd": "cf-origin"` 等价于 `"sd": { "server": "cf-origin" }`。

| 字段 | 说明 |
| --- | --- |
| `server` | 必填，服务发现 Provider tag。 |
| `timeout` | 覆盖全局查询超时。 |
| `strategy` | 覆盖全局地址族策略。 |
| `disable_cache` | 本次查询禁用缓存；false 不能覆盖全局 true，与 DNS 一致。 |
| `disable_optimistic_cache` | 本次查询必须取得新鲜结果。 |
| `rewrite_ttl` | 覆盖新结果的缓存时间，单位秒，0 表示不存储新结果。与 DNS 一样，不会重写已有共享缓存条目的有效期。 |

每次新建节点连接或 UDP PacketConn 时获取结果；已有连接继续使用建立时的地址。
发现失败时报错，不自动回退到静态地址。原始逻辑节点名称和 TLS 配置保持不变。
同一拨号器上的辅助目标不会套用节点地址替换。

已接入公共节点拨号器的协议包括 HTTP、SOCKS、VMess、VLESS、Trojan、Shadowsocks、ShadowTLS、
Snell、SSH、AnyTLS、TUIC、Hysteria、Hysteria2，以及 MASQUE 客户端 endpoint。
支持 TCP、connected UDP、UDP PacketConn 和多网卡拨号。
`server_ports` 和 Hysteria2 `realm` 不能与 `sd` 同时配置。
没有绑定节点地址的其他拨号器会明确拒绝 `sd`，不会静默忽略。

### Cloudflare Provider

`type: "cloudflare"` 必须配置 `api_token`、`zone_id`、`record_id`、`ruleset_id`、`rule_id`，
以及用于解析 API 地址的独立 `domain_resolver`。API 连接支持 `detour`、接口绑定、连接超时等拨号字段。
引导 DNS 和 detour 不能反过来依赖当前 SD Provider。

读取方式与 [natpunch/natcf](https://github.com/csbxd/natpunch/tree/main/natcf) 发布的数据对应：

- IP：读取指定 A/AAAA 记录的 `content`，包括开启代理的记录。
- 端口：读取指定 ruleset，按 `rule_id` 找到 `action_parameters.origin.port`。

Token 需要 DNS 记录读取权限，以及 Origin Ruleset 的读取权限。
只接受 `http_request_origin` 阶段中启用的 `route` 规则，端口范围为 1–65535。
不追踪 CNAME，不支持 Origin Rule 的 host 覆盖。固定访问 Cloudflare 官方 API，不跟随重定向，不写入任何记录或规则。

完整结果的 TTL 来自 DNS 记录；自动 TTL（1）映射为项目默认值 600 秒。
需要更快刷新时设置 `rewrite_ttl`。每次刷新都读取两个资源，但 Cloudflare 对它们的更新并非原子操作，
无法保证得到服务端的同一版本快照。
Origin Rule 中的 TCP 端口不代表 UDP 一定可达；节点必须实际在发现的地址上提供相应协议服务。
