#!/bin/sh
set -eu
# Operator-owned network, separate from web/gateway/New API. Run after Docker on
# boot and before enabling the execution broker. No existing rules are flushed.
public_ip=${1:?Usage: execution-network.sh SERVER_PUBLIC_IPV4}
case "$public_ip" in *[!0-9.]*|'') exit 2 ;; esac
ready_dir=/opt/athena/data/execution-network
install -d -m 0755 "$ready_dir"
rm -f "$ready_dir/ready.json"
docker network inspect athena-execution >/dev/null 2>&1 || docker network create \
  --driver bridge --opt com.docker.network.bridge.name=athena-tools athena-execution >/dev/null
test "$(docker network inspect --format '{{.EnableIPv6}}' athena-execution)" = false
test "$(docker network inspect --format '{{index .Options "com.docker.network.bridge.name"}}' athena-execution)" = athena-tools
# Docker's IP forwarding chain does not necessarily see same-bridge packets.
# Isolate L2 traffic only on our bridge without enabling host-wide br_netfilter.
nft add table bridge athena_execution
nft -f - <<'NFT'
flush table bridge athena_execution
add chain bridge athena_execution forward { type filter hook forward priority 0; policy accept; }
add rule bridge athena_execution forward meta ibrname "athena-tools" counter drop
add chain bridge athena_execution input { type filter hook input priority 0; policy accept; }
add rule bridge athena_execution input meta ibrname "athena-tools" ether type ip6 counter drop
NFT
docker network inspect athena-execution | python3 -c 'import ipaddress,json,sys
network=json.load(sys.stdin)[0]
blocked=[ipaddress.ip_network(value) for value in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")]
subnets=[ipaddress.ip_network(item["Subnet"]) for item in network["IPAM"]["Config"]]
assert subnets and all(subnet.version == 4 and any(subnet.subnet_of(block) for block in blocked) for subnet in subnets), "Execution network requires a private IPv4 subnet"
'
iptables -N ATHENA-EGRESS 2>/dev/null || true
for destination in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4 "$public_ip/32"; do
  iptables -C ATHENA-EGRESS -d "$destination" -j REJECT 2>/dev/null || iptables -A ATHENA-EGRESS -d "$destination" -j REJECT
done
iptables -C DOCKER-USER -i athena-tools -j ATHENA-EGRESS 2>/dev/null || iptables -I DOCKER-USER 1 -i athena-tools -j ATHENA-EGRESS
iptables -C INPUT -i athena-tools -j REJECT 2>/dev/null || iptables -I INPUT 1 -i athena-tools -j REJECT
python3 - "$ready_dir" <<'PY'
import json,os,sys
from pathlib import Path
root=Path(sys.argv[1])
temporary=root/'ready.new'
temporary.write_text(json.dumps({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                 'network':'athena-execution','ipv6':False})+'\n')
temporary.chmod(0o644)
os.replace(temporary,root/'ready.json')
PY
