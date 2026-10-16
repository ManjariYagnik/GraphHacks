"""Fixed, parameterized read-only FalkorDB queries. No input supplies identifiers."""

# Named paths keep every relationship observable (including parallel edges).
REFUND_CONTEXT = """
MATCH (r:Refund {id: $refund_id})
OPTIONAL MATCH requesting=(a:Account)-[:REQUESTED_REFUND]->(r)
OPTIONAL MATCH for_order=(r)-[:FOR_ORDER]->(o:Order)
OPTIONAL MATCH ownership=(owner:Account)-[:PLACED]->(o)
OPTIONAL MATCH usage=(o)-[:USED_DEVICE]->(device:Device)
OPTIONAL MATCH shipping=(o)-[:SHIPPED_TO]->(address:Address)
RETURN r, requesting, for_order, ownership, usage, shipping
LIMIT $limit
"""

EXPANSION_PATTERN = """
MATCH p=(a:Account)-[:PLACED]->(o:Order)-[:USED_DEVICE]->(device:Device)
        <-[:USED_DEVICE]-(other:Order)<-[:PLACED]-(peer:Account)
WHERE a.id IN $frontier AND NOT peer.id IN $visited
  AND o.placed_at >= $start AND o.placed_at <= $end
  AND other.placed_at >= $start AND other.placed_at <= $end
"""
DISCOVER_ACCOUNTS = (
    EXPANSION_PATTERN
    + """
RETURN DISTINCT peer.id ORDER BY peer.id LIMIT $limit
"""
)
DISCOVERY_PATH = (
    EXPANSION_PATTERN
    + """
  AND peer.id = $target
RETURN p ORDER BY a.id, o.id, other.id LIMIT 1
"""
)

ORDERS = """
MATCH (selected:Account)-[:PLACED]->(o:Order)
WHERE selected.id IN $account_ids AND o.placed_at >= $start AND o.placed_at <= $end
WITH DISTINCT o
OPTIONAL MATCH ownership=(a:Account)-[:PLACED]->(o)
OPTIONAL MATCH usage=(o)-[:USED_DEVICE]->(device:Device)
OPTIONAL MATCH shipping=(o)-[:SHIPPED_TO]->(address:Address)
RETURN o, ownership, usage, shipping
ORDER BY o.id, a.id, device.id, address.id LIMIT $limit
"""

REFUNDS = """
MATCH (selected:Account)-[:REQUESTED_REFUND]->(r:Refund)
WHERE selected.id IN $account_ids AND r.requested_at >= $start AND r.requested_at <= $end
WITH DISTINCT r
OPTIONAL MATCH requesting=(a:Account)-[:REQUESTED_REFUND]->(r)
OPTIONAL MATCH for_order=(r)-[:FOR_ORDER]->(o:Order)
OPTIONAL MATCH ownership=(owner:Account)-[:PLACED]->(o)
OPTIONAL MATCH usage=(o)-[:USED_DEVICE]->(device:Device)
OPTIONAL MATCH shipping=(o)-[:SHIPPED_TO]->(address:Address)
RETURN r, requesting, for_order, ownership, usage, shipping
ORDER BY r.id, a.id, o.id, owner.id, device.id, address.id LIMIT $limit
"""

# This aggregation intentionally has no candidate-account filter.
DEVICE_USERS = """
MATCH (a:Account)-[:PLACED]->(o:Order)-[:USED_DEVICE]->(device:Device)
WHERE device.id IN $device_ids AND o.placed_at >= $start AND o.placed_at <= $end
RETURN device.id, count(DISTINCT a.id) ORDER BY device.id LIMIT $limit
"""
