# CSV upload contract

The local Streamlit dashboard accepts one UTF-8 CSV per source entity. UTF-8 BOM is
accepted. The download buttons in **Upload Data** provide empty templates and a
complete synthetic base sample. Both are generated from the current Pydantic source
models and committed JSONL fixtures; evaluation labels are never included.

| Entity file | Required columns |
| --- | --- |
| `accounts.csv` | `id,created_at` |
| `devices.csv` | `id` |
| `addresses.csv` | `id,label` |
| `orders.csv` | `id,account_id,device_id,address_id,placed_at,amount_minor,currency` |
| `refunds.csv` | `id,account_id,order_id,requested_at,amount_minor,currency` |

The filename suggests an entity type; the operator confirms it. Header mapping
offers exact names and a fixed alias list (`entity_id`, `customer_id`,
`shipping_address_id`, `order_date`, `refund_date`, `amount_paise`, etc.). Every
required field must be mapped once. Extra headers must be explicitly ignored.
The application never guesses IDs or relationships. IDs remain strings, so
`acct_0001` keeps its leading zeros. External IDs lacking the required
`acct_`, `dev_`, `addr_`, `ord_`, or `ref_` prefix are errors, not silently
rewritten.

Order foreign keys connect to Account, Device and Address. Refund foreign keys
connect to Account and Order. The referenced records must already be in the
selected graph or in the same batch. A new base dataset needs accounts and orders
and all their referenced entities; it may contain zero refunds. An append may
contain only `refunds.csv`. At most one refund per order is supported.

All timestamps are **integer UTC Unix seconds** after CSV conversion. Input cells
may be unsigned decimal Unix seconds or whole-second ISO 8601 date-times with an
explicit offset, such as `2026-10-15T10:00:00Z` or
`2026-10-15T15:30:00+05:30`. A timezone-free ISO date-time requires the operator
to select a source IANA timezone. DST gaps/ambiguous local times require an
explicit offset. Date-only, locale-specific, fractional-second and implicit
string-to-integer model coercion are rejected. The batch **Data through** field
is a whole-second UTC timestamp; base and append events may not exceed it.

`currency` is exactly `INR`. `amount_minor` is positive integer paise, so
`12345` means INR 123.45. Decimal rupees such as `123.45` are rejected. Address
labels follow `Synthetic address <token>`; this MVP accepts only invented
placeholder addresses and must not be used for personal addresses. Unknown source
fields, malformed quotes, duplicate headers, row-width mismatches, duplicate IDs,
orphan references, wrong owners/currencies, invalid chronology, excess refund
amounts, and second refunds for an order block import.

The server defaults to 2,000,000 bytes and 5,000 data rows per file, 8,000,000
bytes and 12,000 rows per batch. Configure
`RINGLENS_CSV_MAX_FILE_BYTES`, `RINGLENS_CSV_MAX_TOTAL_BYTES`,
`RINGLENS_CSV_MAX_FILE_ROWS`, and `RINGLENS_CSV_MAX_TOTAL_ROWS` in the ignored
`.env`; the backend enforces them even when called without Streamlit. Preview
shows at most eight validated rows per file and total valid row counts.
Validation reports identify filename, physical CSV row, column, reason, suggested
correction, and severity. Downloaded error CSV escapes spreadsheet formula
prefixes. Warnings (for example zero refunds) do not block a valid import.

The adapter converts cells at this boundary, then uses the unchanged strict
`Account`/`Device`/`Address`/`Order`/`Refund` models and cross-record validation.
`make_batch()` generates all five canonical JSONL payload hashes and the manifest
in memory; `prepare_import()` checks the current graph read-only. The importer
receives that validated `DatasetBatch`, preserving batch identity, source hashes,
graph edges, import metadata, and retry semantics from [data-contract.md](data-contract.md)
and [ingestion.md](ingestion.md). No user-authored manifest is required.
