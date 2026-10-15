"""A small synchronous wrapper around the official FalkorDB client.

Reads use GRAPH.RO_QUERY; writes use GRAPH.QUERY. Query text and parameters
must never be included in error messages: both can contain sensitive evidence.
"""

from collections.abc import Callable
from typing import Self, TypeVar

from falkordb import FalkorDB
from falkordb.exceptions import SchemaVersionMismatchException
from falkordb.query_result import QueryResult
from redis.backoff import NoBackoff
from redis.exceptions import AuthenticationError, ConnectionError, RedisError, TimeoutError
from redis.retry import Retry

from ringlens.config import Settings

T = TypeVar("T")


class GraphStoreError(RuntimeError):
    """Safe-to-display database error, without server-provided text or credentials."""


class GraphAuthenticationError(GraphStoreError):
    """The configured credentials were rejected."""


class GraphConnectionError(GraphStoreError):
    """The database could not be reached."""


class GraphTimeoutError(GraphStoreError):
    """A query or network operation exceeded its deadline."""


class GraphQueryError(GraphStoreError):
    """FalkorDB rejected a query."""


class GraphUnavailableError(GraphStoreError):
    """FalkorDB commands are unavailable."""


class GraphStore:
    """Connection scoped to one graph; construction does not create that graph.

    Server query deadlines are milliseconds (1–10,000); socket reads allow 12
    seconds and connection establishment allows 3 seconds. No automatic retries:
    a failed write can have an unknown outcome and must not be blindly replayed.
    """

    def __init__(self, settings: Settings, *, query_timeout_ms: int = 5_000) -> None:
        self._validate_timeout(query_timeout_ms)
        self._query_timeout_ms = query_timeout_ms
        self._closed = False
        password = settings.falkordb_password
        self._db = self._call(
            lambda: FalkorDB(
                host=settings.falkordb_host,
                port=settings.falkordb_port,
                password=password.get_secret_value() if password else None,
                socket_connect_timeout=3,
                socket_timeout=12,
                retry=Retry(NoBackoff(), 0),
            )
        )
        self._graph = self._db.select_graph(settings.graph_name)

    @staticmethod
    def _validate_timeout(value: int) -> None:
        if type(value) is not int or not 1 <= value <= 10_000:
            raise ValueError("query timeout must be an integer between 1 and 10000 milliseconds")

    def _call(self, operation: Callable[[], T], *, write: bool = False) -> T:
        if self._closed:
            raise GraphConnectionError("Graph connection is closed; open a new GraphStore.")
        uncertain = " Write outcome is unknown; inspect the graph before retrying." if write else ""
        try:
            return operation()
        except AuthenticationError:
            raise GraphAuthenticationError(
                "Authentication failed; check RINGLENS_FALKORDB_PASSWORD in .env."
            ) from None
        except TimeoutError:
            raise GraphTimeoutError(
                "Database operation timed out; check server health and query limits." + uncertain
            ) from None
        except ConnectionError:
            raise GraphConnectionError(
                "Cannot connect to FalkorDB; run 'docker compose up -d --wait' and check "
                "RINGLENS_FALKORDB_HOST and RINGLENS_FALKORDB_PORT." + uncertain
            ) from None
        except RedisError as error:
            # The server reports query deadlines as ResponseError, not socket TimeoutError.
            if "timeout" in str(error).lower() or "timed out" in str(error).lower():
                raise GraphTimeoutError(
                    "FalkorDB query exceeded its deadline; inspect query complexity." + uncertain
                ) from None
            raise GraphQueryError(
                "FalkorDB rejected the operation; check Cypher syntax, parameters, "
                "graph existence, and database permissions."
            ) from None
        except SchemaVersionMismatchException:
            raise GraphQueryError(
                "Graph schema changed during the operation; open a new GraphStore for reads. "
                "Inspect the graph before retrying writes."
            ) from None
        except (TypeError, ValueError):
            raise GraphQueryError(
                "The FalkorDB client rejected the operation; check query and parameter types."
            ) from None

    def check_health(self) -> None:
        """Read-only probes, including when the application graph does not exist."""
        if not self._call(self._db.connection.ping):
            raise GraphConnectionError("Database PING failed; check the FalkorDB service.")
        try:
            self._call(lambda: self._db.config_get("TIMEOUT_DEFAULT"))
            self._call(self._db.list_graphs)
        except GraphQueryError:
            raise GraphUnavailableError(
                "FalkorDB probes failed; use the FalkorDB image (not plain Redis) and "
                "allow GRAPH.CONFIG GET and GRAPH.LIST."
            ) from None

    def read(
        self, query: str, params: dict[str, object] | None = None, *, timeout_ms: int | None = None
    ) -> QueryResult:
        """Run a parameterized, server-enforced read-only query on an existing graph."""
        timeout = self._query_timeout_ms if timeout_ms is None else timeout_ms
        self._validate_timeout(timeout)
        return self._call(lambda: self._graph.ro_query(query, params=params, timeout=timeout))

    def write(
        self, query: str, params: dict[str, object] | None = None, *, timeout_ms: int | None = None
    ) -> QueryResult:
        """Run a parameterized write; this may create the selected graph."""
        timeout = self._query_timeout_ms if timeout_ms is None else timeout_ms
        self._validate_timeout(timeout)
        return self._call(
            lambda: self._graph.query(query, params=params, timeout=timeout), write=True
        )

    def close(self) -> None:
        if not self._closed:
            self._db.connection.close()
            self._db.connection.connection_pool.disconnect()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
