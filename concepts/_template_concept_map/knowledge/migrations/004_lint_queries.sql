-- Lint queries (run these before committing / exporting)

-- No-single-child rule violations (exactly 1 child):
SELECT from_node_id, COUNT(*) AS child_count
FROM edge
GROUP BY from_node_id
HAVING child_count = 1;

-- Groups-of-3 violations (non-trunk outgoing edges > 3):
SELECT map_id, from_node_id, COUNT(*) AS non_trunk_children
FROM edge
WHERE is_trunk = 0
GROUP BY map_id, from_node_id
HAVING non_trunk_children > 3;

-- Multiple trunk edges from one node:
SELECT map_id, from_node_id, COUNT(*) AS trunk_children
FROM edge
WHERE is_trunk = 1
GROUP BY map_id, from_node_id
HAVING trunk_children > 1;

-- Missing relationship explanations:
SELECT id, from_node_id, to_node_id, rel_type_id
FROM edge
WHERE trim(why) = '' OR trim(how) = '';

-- Vague relationship type: "related" is not allowed in concept maps.
SELECT id, from_node_id, to_node_id, rel_type_id
FROM edge
WHERE is_trunk = 0 AND rel_type_id = 'rel_related';

-- Crosslink hairball detection: too many non-structural (non-part_of) edges touching one node.
-- If this trips, insert a bridge concept or promote the shared dependency closer to the trunk.
WITH cross_edges AS (
  SELECT from_node_id AS node_id
  FROM edge
  WHERE is_trunk = 0 AND rel_type_id != 'rel_part_of'
  UNION ALL
  SELECT to_node_id AS node_id
  FROM edge
  WHERE is_trunk = 0 AND rel_type_id != 'rel_part_of'
)
SELECT node_id, COUNT(*) AS crosslink_degree
FROM cross_edges
GROUP BY node_id
HAVING crosslink_degree > 3;

-- Depth violations (if you populate depth_hint):
SELECT e.id, e.from_node_id, e.to_node_id, e.depth_hint, m.max_depth
FROM edge e
JOIN map m ON m.id = e.map_id
WHERE e.depth_hint IS NOT NULL AND e.depth_hint > m.max_depth;

-- Duplicate sibling order (optional):
SELECT map_id, from_node_id, branch_order, COUNT(*) AS n
FROM edge
WHERE branch_order IS NOT NULL
GROUP BY map_id, from_node_id, branch_order
HAVING n > 1;

-- Duplicate node labels (should be impossible due to unique index, but kept as a sanity check):
SELECT map_id, label, COUNT(*) AS n
FROM node
GROUP BY map_id, label
HAVING n > 1;
