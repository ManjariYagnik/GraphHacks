"""Fixed, parameterized case persistence and reader queries for FalkorDB."""

CASE = "MATCH (i:Investigation {id: $case_id}) RETURN properties(i)"
HISTORY = """
MATCH (i:Investigation {id: $case_id})-[link:HAS_ASSESSMENT]->(a:Assessment)
RETURN properties(a), id(link) ORDER BY a.created_at, a.id
"""
PENDING_ATTACHMENTS = """
MATCH (i:Investigation {id: $case_id})-[link:HAS_ASSESSMENT]->(a:Assessment)
WHERE link.publication_status = 'LOADING' RETURN a.id
"""
MEMBERS = """
MATCH (i:Investigation {id: $case_id})-[link:INCLUDES]->(a:Account)
RETURN a.id, id(link) ORDER BY a.id
"""
LATEST = """
MATCH (i:Investigation {id: $case_id})-[link:LATEST_ASSESSMENT]->(a:Assessment)
RETURN a.id, a.status, id(link)
"""
EVIDENCE_FOR_HISTORY = """
MATCH (i:Investigation {id: $case_id})-[h:HAS_ASSESSMENT]->(a:Assessment {status: 'READY'})
      -[link:HAS_EVIDENCE]->(e:Evidence)
WHERE coalesce(h.publication_status, 'READY') = 'READY'
RETURN a.id, properties(e), id(link) ORDER BY a.id, e.id
"""
COMPATIBLE_EVIDENCE = """
MATCH (i:Investigation {id: $case_id})-[link:HAS_EVIDENCE]->(e:Evidence)
RETURN e.id, id(link) ORDER BY e.id
"""
OVERLAPS = """
MATCH (i:Investigation {id: $case_id})-[link:OVERLAPS]-(other:Investigation)
WHERE other.publication_status = 'READY'
RETURN other.id, id(link) ORDER BY other.id
"""
REVIEWS = """
MATCH (i:Investigation {id: $case_id})-[link:HAS_REVIEW]->(r:Review)
OPTIONAL MATCH (r)-[about:ABOUT]->(e:Evidence)
RETURN properties(r), id(link), e.id, id(about) ORDER BY r.id, e.id
"""
LIST = """
MATCH (i:Investigation {publication_status: 'READY'})
RETURN i.id ORDER BY i.updated_at DESC, i.id LIMIT $limit
"""
PENDING_CASES = "MATCH (i:Investigation {publication_status: 'LOADING'}) RETURN count(i)"
FIND_OVERLAPS = """
MATCH (i:Investigation {publication_status: 'READY'})-[:INCLUDES]->(a:Account)
WHERE i.dataset_id = $dataset_id AND i.id <> $case_id AND a.id IN $account_ids
RETURN DISTINCT i.id ORDER BY i.id
"""
FLAG_OVERLAPS = """
UNWIND $case_ids AS case_id
MATCH (i:Investigation {id: case_id, publication_status: 'READY'})
SET i.requires_review = true RETURN count(i)
"""
EXISTING_EVIDENCE = "MATCH (e:Evidence) WHERE e.id IN $ids RETURN properties(e)"
EXISTING_ASSESSMENT = "MATCH (a:Assessment {id: $assessment_id}) RETURN properties(a)"

START = """
MERGE (i:Investigation {id: $case_id}) ON CREATE SET i += $case_properties
MERGE (a:Assessment {id: $assessment_id}) ON CREATE SET a += $assessment_properties
MERGE (i)-[h:HAS_ASSESSMENT]->(a) ON CREATE SET h.publication_status = 'LOADING'
RETURN count(a)
"""
INCLUDE = """
UNWIND $account_ids AS account_id
MATCH (i:Investigation {id: $case_id}), (a:Account {id: account_id})
MERGE (i)-[:INCLUDES]->(a) RETURN count(a)
"""
EVIDENCE = """
UNWIND $rows AS row
MATCH (a:Assessment {id: $assessment_id})
MERGE (e:Evidence {id: row.id}) ON CREATE SET e += row
MERGE (a)-[:HAS_EVIDENCE]->(e) RETURN count(e)
"""
LINK_OVERLAPS = """
UNWIND $pairs AS pair
MATCH (a:Investigation {id: pair.left}), (b:Investigation {id: pair.right})
MERGE (a)-[:OVERLAPS]->(b) RETURN count(b)
"""
STAGED_EVIDENCE = """
MATCH (a:Assessment {id: $assessment_id})-[link:HAS_EVIDENCE]->(e:Evidence)
RETURN properties(e), id(link) ORDER BY e.id
"""
STAGED_OVERLAPS = """
MATCH (i:Investigation {id: $case_id})-[link:OVERLAPS]-(other:Investigation)
RETURN other.id, id(link) ORDER BY other.id
"""

# Final publication is one query after endpoint/content checks. The evidence
# variant matches evidence before publication; status changes only for policy reopening.
PUBLISH_START = """
MATCH (d:Dataset {id: $dataset_id}),
      (i:Investigation {id: $case_id})-[h:HAS_ASSESSMENT]->(a:Assessment {id: $assessment_id})
WHERE d.status = 'READY' AND d.as_of = $horizon AND a.status IN ['LOADING', 'READY']
  AND a.analytical_hash = $analytical_hash AND i.group_key = $group_key
"""
PUBLISH_EVIDENCE = """
MATCH (a)-[:HAS_EVIDENCE]->(e:Evidence)
MERGE (i)-[:HAS_EVIDENCE]->(e)
WITH DISTINCT i, a, h
"""
PUBLISH_END = """
OPTIONAL MATCH (i)-[old:LATEST_ASSESSMENT]->(:Assessment)
DELETE old
WITH i, a, h
MERGE (i)-[:LATEST_ASSESSMENT]->(a)
SET a.status = 'READY', a.completed_at = coalesce(a.completed_at, $completed_at),
    h.publication_status = 'READY',
    i.publication_status = 'READY', i.latest_assessment_id = a.id,
    i.priority = a.priority, i.score = a.score, i.rule_version = a.rule_version,
    i.assessment_as_of = a.window_end, i.updated_at = $completed_at,
    i.requires_review = i.requires_review OR i.status = 'CLOSED' OR $has_overlaps,
    i.status = CASE WHEN $reopen THEN 'REOPENED' ELSE i.status END
WITH i, a
OPTIONAL MATCH (i)-[:OVERLAPS]-(other:Investigation)
SET other.requires_review = true
RETURN count(DISTINCT a)
"""
PUBLISH = PUBLISH_START + " WITH i, a, h " + PUBLISH_END
PUBLISH_WITH_EVIDENCE = PUBLISH_START + PUBLISH_EVIDENCE + PUBLISH_END
