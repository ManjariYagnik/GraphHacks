"""Fixed RingLens graph vocabulary and additive range-index initialization."""

import time
from dataclasses import dataclass

from ringlens.graph_store import GraphQueryError, GraphStore, GraphStoreError
from ringlens.models import SourceRecord
from ringlens.validation import source_hash

LABELS = (
    "Account",
    "Device",
    "Address",
    "Order",
    "Refund",
    "Investigation",
    "Evidence",
    "Review",
    "Dataset",
    "ImportBatch",
    "Assessment",
)
RELATIONSHIPS = (
    ("Account", "PLACED", "Order"),
    ("Order", "USED_DEVICE", "Device"),
    ("Order", "SHIPPED_TO", "Address"),
    ("Account", "REQUESTED_REFUND", "Refund"),
    ("Refund", "FOR_ORDER", "Order"),
    ("Investigation", "INCLUDES", "Account"),
    ("Investigation", "HAS_EVIDENCE", "Evidence"),
    ("Investigation", "HAS_REVIEW", "Review"),
    ("Review", "ABOUT", "Evidence"),
    ("Dataset", "HAS_BATCH", "ImportBatch"),
    ("Investigation", "HAS_ASSESSMENT", "Assessment"),
    ("Investigation", "LATEST_ASSESSMENT", "Assessment"),
    ("Assessment", "HAS_EVIDENCE", "Evidence"),
    ("Investigation", "OVERLAPS", "Investigation"),
)
RELATIONSHIP_TYPES = frozenset(relation for _, relation, _ in RELATIONSHIPS)

# Only these source fields become edges; retain them in source_hash, not properties.
SOURCE_REFERENCE_FIELDS = {
    "Account": frozenset(),
    "Device": frozenset(),
    "Address": frozenset(),
    "Order": frozenset({"account_id", "device_id", "address_id"}),
    "Refund": frozenset({"account_id", "order_id"}),
}

INDEXES = tuple((label, "id") for label in LABELS) + (
    ("Account", "created_at"),
    ("Order", "placed_at"),
    ("Refund", "requested_at"),
    ("Investigation", "assessment_as_of"),
    ("Investigation", "updated_at"),
    ("Evidence", "window_start"),
    ("Evidence", "window_end"),
    ("Review", "reviewed_at"),
    ("Dataset", "as_of"),
    ("ImportBatch", "started_at"),
    ("Investigation", "group_key"),
    ("Assessment", "window_end"),
    ("Assessment", "created_at"),
    ("Assessment", "input_revision"),
)
INDEX_CATALOG_QUERY = (
    "CALL db.indexes() YIELD label, types, entitytype, status "
    "RETURN label, types, entitytype, status"
)
REFUND_CONTEXT_QUERY = """
MATCH (account:Account)-[:REQUESTED_REFUND]->(refund:Refund {id: $refund_id})
      -[:FOR_ORDER]->(order:Order),
      (account)-[:PLACED]->(order),
      (order)-[:USED_DEVICE]->(device:Device),
      (order)-[:SHIPPED_TO]->(address:Address)
RETURN account.id, refund.id, order.id, device.id, address.id, order.placed_at
"""


class SchemaInitializationError(GraphStoreError):
    """Schema initialization did not finish; existing data remains intact."""


@dataclass(frozen=True)
class SchemaResult:
    created_indexes: int
    existing_indexes: int


def source_node_properties(record: SourceRecord) -> dict[str, object]:
    """Prepare scalar source properties and importer-managed conflict fingerprint."""
    fingerprint = source_hash(record)  # Also revalidates the exact source model type.
    references = SOURCE_REFERENCE_FIELDS[type(record).__name__]
    return {**record.model_dump(exclude=references), "source_hash": fingerprint}


def _range_indexes(store: GraphStore) -> dict[tuple[str, str], str]:
    indexes = {}
    for label, types, entity_type, status in store.read(INDEX_CATALOG_QUERY).result_set:
        if entity_type == "NODE":
            if not isinstance(types, dict):
                raise SchemaInitializationError(
                    "Unexpected index metadata; use the pinned FalkorDB v4.20.7 image."
                )
            for property_name, field_types in types.items():
                if "RANGE" in field_types:
                    indexes[(label, property_name)] = status
    return indexes


def initialize_schema(store: GraphStore, *, wait_seconds: float = 30) -> SchemaResult:
    """Create only missing indexes, then wait for them to become operational.

    Identifiers come exclusively from INDEXES. No data, constraints, or existing
    indexes are deleted/replaced. Run one initializer/importer per graph at a time.
    """
    if not 0 < wait_seconds <= 60:
        raise ValueError("wait_seconds must be between 0 (exclusive) and 60")
    existing = _range_indexes(store) if store.graph_exists() else {}
    created = 0
    for label, property_name in INDEXES:
        if (label, property_name) in existing:
            continue
        # Cypher identifiers cannot be parameters; these are fixed code constants.
        query = f"CREATE INDEX FOR (n:{label}) ON (n.{property_name})"
        try:
            store.write(query)
            created += 1
        except GraphQueryError:
            # Handle another initializer creating the same index, without hiding
            # unrelated errors or relying on the text of server exceptions.
            if (label, property_name) not in _range_indexes(store):
                raise

    deadline = time.monotonic() + wait_seconds
    while True:
        current = _range_indexes(store)
        if all(current.get(index) == "OPERATIONAL" for index in INDEXES):
            return SchemaResult(created_indexes=created, existing_indexes=len(INDEXES) - created)
        if time.monotonic() >= deadline:
            raise SchemaInitializationError(
                "Indexes are not operational yet; check database health and rerun schema init. "
                "Existing data and indexes were preserved."
            )
        time.sleep(0.1)
