"""Fixed human-review and standalone-assessment queries; no model supplies Cypher."""

REVIEWS = """
MATCH (r:Review {dataset_id: $dataset_id, publication_status: 'READY'})
RETURN properties(r) ORDER BY r.reviewed_at, r.id LIMIT $limit
"""
REVIEW = "MATCH (r:Review {id: $id}) RETURN properties(r)"
SUPERSEDERS = """
MATCH (r:Review {publication_status: 'READY'})-[:SUPERSEDES]->(old:Review {id: $id})
RETURN r.id
"""
START_ASSESSMENT = """
MERGE (a:Assessment {id: $id}) ON CREATE SET a += $properties RETURN count(a)
"""
FINISH_ASSESSMENT = """
MATCH (d:Dataset {id: $dataset_id}), (a:Assessment {id: $id})
WHERE d.status = 'READY' AND d.as_of = $horizon AND a.status = 'LOADING'
SET a.status = 'READY', a.completed_at = $completed_at RETURN count(a)
"""
START_REVIEW = """
MATCH (a:Assessment {id: $assessment_id, status: 'READY'})
MERGE (r:Review {id: $id}) ON CREATE SET r += $properties
MERGE (r)-[:REVIEWS_ASSESSMENT]->(a) RETURN count(r)
"""
LINK_EVIDENCE = """
UNWIND $evidence_ids AS eid
MATCH (r:Review {id: $id}), (e:Evidence {id: eid})
MERGE (r)-[:ABOUT]->(e) RETURN count(e)
"""
LINK_SUPERSEDES = """
MATCH (r:Review {id: $id}), (old:Review {id: $supersedes, publication_status: 'READY'})
MERGE (r)-[:SUPERSEDES]->(old) RETURN count(old)
"""
LINK_CASE = """
MATCH (i:Investigation {id: $case_id, publication_status: 'READY'}), (r:Review {id: $id})
MERGE (i)-[:HAS_REVIEW]->(r) RETURN count(i)
"""
FINISH_REVIEW = """
MATCH (d:Dataset {id: $dataset_id}), (r:Review {id: $id})
WHERE d.status = 'READY' AND d.as_of = $horizon
SET r.publication_status = 'READY' RETURN count(r)
"""
FINISH_REVIEW_CASE = """
MATCH (d:Dataset {id: $dataset_id}), (i:Investigation {id: $case_id}), (r:Review {id: $id})
WHERE d.status = 'READY' AND d.as_of = $horizon
SET r.publication_status = 'READY', i.status = $case_status, i.updated_at = $recorded_at
RETURN count(r)
"""
REVIEW_LINKS = """
MATCH (r:Review {id: $id})-[link]->(target)
RETURN type(link), target.id ORDER BY type(link), target.id
"""
