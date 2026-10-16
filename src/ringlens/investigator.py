"""FalkorDB discovers relationships; Python collects provenance and evaluates v1 rules."""

from hashlib import sha256

from falkordb.path import Path as GraphPath
from pydantic import TypeAdapter, ValidationError

from ringlens import import_queries as metadata_q
from ringlens import investigation_queries as q
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.investigation_models import (
    CandidateAccount,
    EvidenceFinding,
    InvestigationConfig,
    InvestigationIssue,
    InvestigationResult,
    ObservationWindow,
    PathProof,
)
from ringlens.models import FILE_KINDS, AccountId, Evidence, RefundId, Timestamp, canonical_json
from ringlens.scoring import score_records
from ringlens.validation import SourceRecords, validate_records
from ringlens.verify import GraphState, graph_records, path_json, read_history


class InvestigationInputError(ValueError):
    """Invalid caller parameters, distinct from missing data and incomplete work."""


class InvestigationDataUnavailable(ValueError):
    """Missing seed or no successfully imported READY dataset."""


class _Incomplete(ValueError):
    pass


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


class _Reader:
    def __init__(self, store: GraphStore, result: InvestigationResult):
        self.store, self.result = store, result
        self.query_id = "graph.exists.v1"

    def read(self, identifier, query, params=None, *, limit=None):
        self.query_id = identifier
        if identifier not in self.result.query_ids:
            self.result.query_ids.append(identifier)
        rows = self.store.read(query, params).result_set
        if limit is not None and len(rows) > limit:
            raise _Incomplete(
                f"{identifier} exceeded its {limit}-row bound; increase --max-evidence."
            )
        return rows


def _metadata(reader: _Reader):
    """Reuse Phase 4 metadata validation without loading all source nodes/edges."""
    bound = reader.result.config.max_evidence
    params = {"limit": bound + 1}
    state = GraphState()
    state.datasets = reader.read(
        "dataset.v1", metadata_q.READ_DATASETS + " LIMIT $limit", params, limit=bound
    )
    if not state.datasets:
        raise InvestigationDataUnavailable(
            "No Dataset exists; import a validated base batch first."
        )
    if len(state.datasets) != 1 or state.datasets[0][1].get("status") != "READY":
        raise InvestigationDataUnavailable(
            "Dataset must be READY; finish or retry ingestion first."
        )
    state.batches = reader.read(
        "import_batches.v1", metadata_q.READ_BATCHES + " LIMIT $limit", params, limit=bound
    )
    state.edges = reader.read(
        "import_provenance.v1",
        metadata_q.READ_EDGES + " LIMIT $limit",
        {**params, "types": ["HAS_BATCH"]},
        limit=bound,
    )
    dataset, history = read_history(state)
    if not history or any(item.batch.status != "READY" for item in history):
        raise InvestigationDataUnavailable(
            "All imported batches must be READY before investigation."
        )
    if dataset.as_of != max(item.manifest.as_of for item in history):
        raise InvestigationDataUnavailable(
            "Dataset horizon disagrees with successful import history."
        )
    descriptor = {
        "dataset_id": dataset.id,
        "schema_version": dataset.schema_version,
        "as_of": dataset.as_of,
        "batches": sorted(
            [
                {
                    "id": item.batch.id,
                    "content_hash": item.batch.content_hash,
                    "mode": item.batch.mode,
                    "as_of": item.manifest.as_of,
                }
                for item in history
            ],
            key=lambda item: item["id"],
        ),
    }
    return dataset, descriptor, _digest(descriptor)


class _Sources:
    """Only graph-returned context/evidence records, never a traversal substitute."""

    def __init__(self):
        self.nodes = {}
        self.edges = {}

    def add(self, paths):
        if any(not isinstance(path, GraphPath) for path in paths):
            raise ValueError("A required source relationship is missing from the graph.")
        for path in paths:
            for node in path.nodes():
                self.nodes[node.id] = node
            for edge in path.edges():
                self.edges[edge.id] = edge

    def records(self, horizon):
        state = GraphState()
        labels = {label: kind for kind, label in metadata_q.SOURCE_LABELS.items()}
        for node in self.nodes.values():
            if len(node.labels) != 1 or node.labels[0] not in labels:
                raise ValueError("An investigation path has an unexpected source label.")
            state.nodes[labels[node.labels[0]]].append((node.id, dict(node.properties)))
        state.edges = [
            [edge.src_node, edge.relation, edge.dest_node, dict(edge.properties)]
            for edge in self.edges.values()
        ]
        records = graph_records(state)
        validate_records(records, as_of=horizon)
        return SourceRecords(
            **{kind: sorted(getattr(records, kind), key=lambda row: row.id) for kind in FILE_KINDS}
        )


def _proof(result, path, query_id):
    if not isinstance(path, GraphPath):
        raise ValueError("FalkorDB did not return a required explanation path.")
    encoded = path_json(path)
    ids = {node["graph_id"]: node["id"] for node in encoded["nodes"]}
    # Physical IDs remain in the explanation but never enter analytical identities.
    semantic = {
        "nodes": [
            {key: value for key, value in node.items() if key != "graph_id"}
            for node in encoded["nodes"]
        ],
        "edges": [
            {
                "type": edge["type"],
                "source": ids[edge["source_graph_id"]],
                "target": ids[edge["target_graph_id"]],
                "properties": edge["properties"],
            }
            for edge in encoded["edges"]
        ],
        "query_id": query_id,
        "window": result.window.model_dump(),
    }
    id_ = "path_" + _digest(semantic)
    if not any(proof.id == id_ for proof in result.paths):
        if len(result.paths) >= result.config.max_evidence:
            raise _Incomplete("Returned path evidence reached --max-evidence; increase the bound.")
        result.paths.append(
            PathProof(
                id=id_,
                query_id=query_id,
                window=result.window,
                source_ids=sorted(set(ids.values())),
                path=encoded,
            )
        )
    return id_


def _findings(result, analysis):
    findings = []
    for rule in analysis.rules:
        if not rule.triggered:
            continue
        event_ids = {id_ for id_ in rule.source_ids if id_.startswith(("ord_", "ref_"))}
        proofs = [
            proof
            for proof in result.paths
            if proof.query_id in {"orders.v1", "refunds.v1", "refund_context.v1"}
            and event_ids.intersection(proof.source_ids)
        ]
        path_ids = sorted({proof.id for proof in proofs})
        query_ids = sorted({proof.query_id for proof in proofs})
        if rule.rule_id == "R1":
            query_ids.append("device_users.v1")
            query_ids.sort()
        details = {
            **rule.details,
            "path_ids": path_ids,
            "query_ids": query_ids,
            "scoring_config": result.config.scoring.model_dump(),
        }
        identity = {
            "rule_id": rule.rule_id,
            "rule_version": result.rule_version,
            "source_ids": rule.source_ids,
            "window": result.window.model_dump(),
            "details": details,
        }
        evidence = Evidence(
            id="ev_" + _digest(identity),
            rule_id=rule.rule_id,
            rule_version=result.rule_version,
            window_start=result.window.start,
            window_end=result.window.end,
            contribution=rule.contribution,
            source_ids=rule.source_ids,
            details_json=canonical_json(details),
        )
        findings.append(
            EvidenceFinding(
                evidence=evidence,
                explanation=rule.explanation,
                path_ids=path_ids,
                query_ids=query_ids,
            )
        )
    return findings


def investigate(
    store: GraphStore,
    refund_id: str,
    *,
    as_of: int | None = None,
    config: InvestigationConfig | None = None,
) -> InvestigationResult:
    """Read-only, bounded, two-expansion assessment. No runtime fixture/evaluation I/O."""
    try:
        refund_id = TypeAdapter(RefundId).validate_python(refund_id, strict=True)
        if as_of is not None:
            as_of = TypeAdapter(Timestamp).validate_python(as_of, strict=True)
        config = InvestigationConfig.model_validate(
            config if config is not None else InvestigationConfig()
        )
    except ValidationError:
        raise InvestigationInputError(
            "Invalid refund ID, UTC timestamp, or investigation bounds."
        ) from None
    result = InvestigationResult(seed_refund_id=refund_id, config=config)
    reader, sources = _Reader(store, result), _Sources()
    started = False
    try:
        if not store.graph_exists():
            raise InvestigationDataUnavailable(
                "Graph has no imported dataset; ingest the base first."
            )
        dataset, descriptor, revision = _metadata(reader)
        assessment = dataset.as_of if as_of is None else as_of
        if assessment > dataset.as_of:
            raise InvestigationInputError(
                "Assessment time exceeds the successfully imported dataset horizon."
            )
        window = ObservationWindow(
            start=max(0, assessment - config.window_days * 86400), end=assessment
        )
        result = result.model_copy(
            update={
                "dataset_id": dataset.id,
                "input_revision": revision,
                "successful_batch_ids": [item["id"] for item in descriptor["batches"]],
                "assessment_as_of": assessment,
                "window": window,
            }
        )
        reader.result = result
        context = reader.read(
            "refund_context.v1", q.REFUND_CONTEXT, {"refund_id": refund_id, "limit": 2}
        )
        if not context:
            raise InvestigationDataUnavailable("Refund does not exist in the selected graph.")
        if len(context) != 1:
            raise ValueError(
                "Refund context is ambiguous: required relationship cardinality is inconsistent."
            )
        sources.add(context[0][1:])
        result = result.model_copy(update={"source_records": sources.records(dataset.as_of)})
        reader.result = result
        seed = next(row for row in result.source_records.refunds if row.id == refund_id)
        if not window.start <= seed.requested_at <= window.end:
            raise InvestigationInputError("Seed refund is outside the inclusive assessment window.")
        result = result.model_copy(update={"seed_account_id": seed.account_id})
        reader.result = result
        started = True
        result.candidates.append(CandidateAccount(id=seed.account_id, depth=0))
        seed_paths = [_proof(result, path, "refund_context.v1") for path in context[0][1:]]
        result.candidates[0] = CandidateAccount(
            id=seed.account_id, depth=0, path_ids=sorted(seed_paths)
        )
        visited, frontier = {seed.account_id}, [seed.account_id]
        params = {"start": window.start, "end": window.end}
        for depth in (1, 2):
            if not frontier:
                break
            remaining = config.max_accounts - len(visited)
            expansion_params = {
                **params,
                "frontier": sorted(frontier),
                "visited": sorted(visited),
                "limit": remaining + 1,
            }
            rows = reader.read(f"expansion_{depth}.v1", q.DISCOVER_ACCOUNTS, expansion_params)
            targets = [
                TypeAdapter(AccountId).validate_python(row[0], strict=True)
                for row in rows[:remaining]
            ]
            for target in targets:
                result.candidates.append(CandidateAccount(id=target, depth=depth))
                paths = reader.read(
                    f"expansion_{depth}_path.v1",
                    q.DISCOVERY_PATH,
                    {**expansion_params, "target": target},
                )
                if len(paths) != 1:
                    raise _Incomplete(
                        "A discovered account no longer has its required device path."
                    )
                proof = _proof(result, paths[0][0], f"expansion_{depth}_path.v1")
                reached_from = paths[0][0].nodes()[0].properties["id"]
                result.candidates[-1] = CandidateAccount(
                    id=target,
                    depth=depth,
                    reached_from=reached_from,
                    path_ids=[proof],
                )
            visited.update(targets)
            if len(rows) > remaining:
                raise _Incomplete("Device discovery exceeded --max-accounts; increase the bound.")
            frontier = targets
        gather_params = {**params, "account_ids": sorted(visited), "limit": config.max_evidence + 1}
        for identifier, query in (("orders.v1", q.ORDERS), ("refunds.v1", q.REFUNDS)):
            rows = reader.read(identifier, query, gather_params, limit=config.max_evidence)
            for row in rows:
                sources.add(row[1:])
                for path in row[1:]:
                    _proof(result, path, identifier)
            result = result.model_copy(update={"source_records": sources.records(dataset.as_of)})
            reader.result = result
        devices = sorted(
            {
                order.device_id
                for order in result.source_records.orders
                if order.account_id in visited and window.start <= order.placed_at <= window.end
            }
        )
        counts = reader.read(
            "device_users.v1",
            q.DEVICE_USERS,
            {**gather_params, "device_ids": devices},
            limit=config.max_evidence,
        )
        result.device_user_counts.update({device: count for device, count in counts})
        analysis = score_records(
            result.source_records,
            candidate_ids=sorted(visited),
            window=window,
            device_user_counts=result.device_user_counts,
            config=config.scoring,
        )
        _, _, final_revision = _metadata(reader)
        if final_revision != revision:
            raise _Incomplete(
                "Successful import metadata changed during investigation; retry on READY data."
            )
        findings = _findings(result, analysis)
        explanation = " ".join(rule.explanation for rule in analysis.rules if rule.triggered)
        if not explanation:
            explanation = "No configured prioritization rules were triggered in this window."
        result = result.model_copy(
            update={
                "completeness": "COMPLETE",
                "analysis": analysis,
                "total_score": analysis.total,
                "priority": analysis.priority,
                "triggered_rules": findings,
                "explanation": explanation,
                "mitigating_context": analysis.mitigating_context,
            }
        )
    except InvestigationInputError:
        raise
    except InvestigationDataUnavailable as error:
        if not started:
            raise
        result.errors.append(
            InvestigationIssue(
                code="DATA_CHANGED",
                message=str(error),
                query_id=reader.query_id,
            )
        )
    except (GraphStoreError, _Incomplete, ValueError) as error:
        code = (
            "QUERY_FAILED"
            if isinstance(error, GraphStoreError)
            else "BOUND_OR_MISSING_WORK"
            if isinstance(error, _Incomplete)
            else "INCONSISTENT_DATA"
        )
        message = (
            str(error)
            if not isinstance(error, ValidationError)
            else "Graph source records violate the validated contract."
        )
        result.errors.append(
            InvestigationIssue(code=code, message=message, query_id=reader.query_id)
        )
    result.candidates.sort(key=lambda candidate: (candidate.depth, candidate.id))
    result.paths.sort(key=lambda proof: proof.id)
    result.limitations.extend(
        [
            "Two device expansions are evaluated; more distant accounts may exist.",
            "Addresses support evidence and are not traversal links.",
            "Demonstration priority is not a calibrated probability or proof of abuse.",
            "Read queries are not a multi-query snapshot; import metadata is checked "
            "before and after.",
        ]
    )
    return InvestigationResult.model_validate(result)


def render_text(result: InvestigationResult) -> str:
    lines = [
        f"{result.completeness}: {result.seed_refund_id}",
        f"Account: {result.seed_account_id or 'unavailable'}",
        f"Assessment as_of: {result.assessment_as_of}; window: {result.window}",
    ]
    if result.completeness == "COMPLETE":
        lines.append(
            f"Priority: {result.priority}; score: {result.total_score}/100 ({result.rule_version})"
        )
    else:
        lines.append("No final score or priority; investigation is incomplete.")
    lines.append("Candidates: " + ", ".join(f"{c.id} (depth {c.depth})" for c in result.candidates))
    if not result.triggered_rules:
        lines.append(result.explanation)
    for finding in result.triggered_rules:
        lines.append(
            f"{finding.evidence.rule_id} +{finding.evidence.contribution:g}: "
            f"{finding.explanation} Evidence {finding.evidence.id}"
        )
    for proof in result.paths:
        if proof.query_id not in {
            "expansion_1_path.v1",
            "expansion_2_path.v1",
            "refund_context.v1",
        }:
            continue
        nodes, edges = proof.path["nodes"], proof.path["edges"]
        path = nodes[0]["id"]
        for index, edge in enumerate(edges):
            forward = edge["source_graph_id"] == nodes[index]["graph_id"]
            path += f" -[{edge['type']}]-> " if forward else f" <-[{edge['type']}]- "
            path += nodes[index + 1]["id"]
        lines.append(f"Path ({proof.query_id}): {path}")
    lines.extend(f"Context: {message}" for message in result.mitigating_context)
    lines.extend(
        f"Error [{issue.code}] ({issue.query_id}): {issue.message}" for issue in result.errors
    )
    lines.extend(f"Limitation: {message}" for message in result.limitations)
    return "\n".join(lines)
