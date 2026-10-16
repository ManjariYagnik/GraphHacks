"""Fixed FalkorDB queries; data supplies values, never labels or relationship types."""

SOURCE_LABELS = {
    "accounts": "Account",
    "devices": "Device",
    "addresses": "Address",
    "orders": "Order",
    "refunds": "Refund",
}
SOURCE_RELATIONS = ("PLACED", "USED_DEVICE", "SHIPPED_TO", "REQUESTED_REFUND", "FOR_ORDER")
READ_NODES = {
    kind: f"MATCH (n:{label}) RETURN id(n), properties(n)" for kind, label in SOURCE_LABELS.items()
}
READ_DATASETS = "MATCH (n:Dataset) RETURN id(n), properties(n)"
READ_BATCHES = "MATCH (n:ImportBatch) RETURN id(n), properties(n)"
READ_EDGES = """
MATCH (a)-[r]->(b) WHERE type(r) IN $types
RETURN id(a), type(r), id(b), properties(r)
"""
WRITE_NODES = {
    kind: f"""UNWIND $rows AS row MERGE (n:{label} {{id: row.id}})
              ON CREATE SET n += row RETURN count(n)"""
    for kind, label in SOURCE_LABELS.items()
}
EDGE_SPECS = (
    ("orders", "PLACED", "Account", "Order", "account_id", "id"),
    ("orders", "USED_DEVICE", "Order", "Device", "id", "device_id"),
    ("orders", "SHIPPED_TO", "Order", "Address", "id", "address_id"),
    ("refunds", "REQUESTED_REFUND", "Account", "Refund", "account_id", "id"),
    ("refunds", "FOR_ORDER", "Refund", "Order", "id", "order_id"),
)
WRITE_EDGES = {
    relation: f"""UNWIND $rows AS row
        MATCH (a:{start} {{id: row.{start_field}}}), (b:{end} {{id: row.{end_field}}})
        MERGE (a)-[:{relation}]->(b) RETURN count(b)"""
    for _, relation, start, end, start_field, end_field in EDGE_SPECS
}

# Manifest and membership are importer provenance on HAS_BATCH, leaving the
# strict Dataset/ImportBatch record properties unchanged. JSON is stored as a string.
START_BATCH = """
MERGE (d:Dataset {id: $dataset_id})
ON CREATE SET d.schema_version = $schema_version, d.as_of = $as_of
SET d.status = 'INGESTING'
MERGE (b:ImportBatch {id: $batch_id})
ON CREATE SET b.content_hash = $content_hash, b.mode = $mode, b.started_at = $started_at
SET b.status = 'LOADING'
REMOVE b.completed_at, b.error
MERGE (d)-[r:HAS_BATCH]->(b)
ON CREATE SET r.manifest_json = $manifest_json, r.source_ids = $source_ids
RETURN count(b)
"""
FINISH_BATCH = """
MATCH (d:Dataset {id: $dataset_id})-[:HAS_BATCH]->(b:ImportBatch {id: $batch_id})
WHERE d.status = 'INGESTING' AND b.status = 'LOADING'
SET b.status = 'READY', b.completed_at = $completed_at, d.status = 'READY', d.as_of = $as_of
REMOVE b.error RETURN count(b)
"""
FAIL_BATCH = """
MATCH (d:Dataset {id: $dataset_id})-[:HAS_BATCH]->(b:ImportBatch {id: $batch_id})
WHERE b.status = 'LOADING'
SET d.status = 'FAILED', b.status = 'FAILED', b.completed_at = $completed_at, b.error = $error
RETURN count(b)
"""

REFUND_PATHS = """
MATCH p=(r:Refund {id: $refund_id})-[:FOR_ORDER]->(o:Order)-[:USED_DEVICE]->(device:Device),
      requesting=(a:Account)-[:REQUESTED_REFUND]->(r),
      shipping=(o)-[:SHIPPED_TO]->(address:Address), (a)-[:PLACED]->(o)
WHERE r.requested_at <= $as_of AND o.placed_at >= $window_start AND o.placed_at <= $as_of
RETURN p, requesting, shipping LIMIT $limit
"""
PEER_PATTERN = """
MATCH p=(a:Account {id: $account_id})-[:PLACED]->(o:Order)-[:USED_DEVICE]->(device:Device)
        <-[:USED_DEVICE]-(other:Order)<-[:PLACED]-(peer:Account)
WHERE peer.id <> a.id AND o.placed_at >= $window_start AND o.placed_at <= $as_of
  AND other.placed_at >= $window_start AND other.placed_at <= $as_of
"""
PEER_PATHS = PEER_PATTERN + " RETURN p LIMIT $limit"
PEER_COUNT = PEER_PATTERN + " RETURN count(DISTINCT peer.id)"
