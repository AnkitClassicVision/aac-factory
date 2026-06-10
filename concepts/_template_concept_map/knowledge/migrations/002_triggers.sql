PRAGMA foreign_keys = ON;

CREATE TRIGGER IF NOT EXISTS enforce_max_children
BEFORE INSERT ON edge
BEGIN
  SELECT CASE
    WHEN (
      (SELECT COUNT(*) FROM edge WHERE map_id = NEW.map_id AND from_node_id = NEW.from_node_id)
      >= (SELECT max_children FROM map WHERE id = NEW.map_id)
    )
    THEN RAISE(ABORT, 'max_children exceeded for from_node_id')
  END;
END;

-- Trunk edges are the logic chain; each node may have at most one trunk continuation.
CREATE TRIGGER IF NOT EXISTS enforce_single_trunk_outgoing
BEFORE INSERT ON edge
WHEN NEW.is_trunk = 1
BEGIN
  SELECT CASE
    WHEN (
      (SELECT COUNT(*) FROM edge
       WHERE map_id = NEW.map_id AND from_node_id = NEW.from_node_id AND is_trunk = 1)
      >= 1
    )
    THEN RAISE(ABORT, 'multiple trunk edges from a single node are not allowed')
  END;
END;

-- Groups-of-3 rule: limit non-trunk outgoing edges (supports/crosslinks) to 3 per node.
CREATE TRIGGER IF NOT EXISTS enforce_max_non_trunk_children
BEFORE INSERT ON edge
WHEN NEW.is_trunk = 0
BEGIN
  SELECT CASE
    WHEN (
      (SELECT COUNT(*) FROM edge
       WHERE map_id = NEW.map_id AND from_node_id = NEW.from_node_id AND is_trunk = 0)
      >= 3
    )
    THEN RAISE(ABORT, 'max non-trunk children exceeded for from_node_id')
  END;
END;

-- Keep the structure fractal/tree-like: each node has at most one structural (part_of) parent.
CREATE TRIGGER IF NOT EXISTS enforce_single_structural_parent
BEFORE INSERT ON edge
WHEN NEW.is_trunk = 0 AND NEW.rel_type_id = 'rel_part_of'
BEGIN
  SELECT CASE
    WHEN (
      (SELECT COUNT(*) FROM edge
       WHERE map_id = NEW.map_id AND to_node_id = NEW.to_node_id
         AND is_trunk = 0 AND rel_type_id = 'rel_part_of')
      >= 1
    )
    THEN RAISE(ABORT, 'multiple structural parents (part_of) are not allowed')
  END;
END;

-- Every edge must carry a meaningful relationship explanation.
CREATE TRIGGER IF NOT EXISTS enforce_edge_why_how
BEFORE INSERT ON edge
BEGIN
  SELECT CASE WHEN trim(NEW.why) = '' THEN RAISE(ABORT, 'edge.why is required') END;
  SELECT CASE WHEN trim(NEW.how) = '' THEN RAISE(ABORT, 'edge.how is required') END;
END;

CREATE TRIGGER IF NOT EXISTS enforce_edge_why_how_update
BEFORE UPDATE ON edge
BEGIN
  SELECT CASE WHEN trim(NEW.why) = '' THEN RAISE(ABORT, 'edge.why is required') END;
  SELECT CASE WHEN trim(NEW.how) = '' THEN RAISE(ABORT, 'edge.how is required') END;
END;

CREATE TRIGGER IF NOT EXISTS node_updated_at
AFTER UPDATE ON node
BEGIN
  UPDATE node SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS edge_updated_at
AFTER UPDATE ON edge
BEGIN
  UPDATE edge SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS map_updated_at
AFTER UPDATE ON map
BEGIN
  UPDATE map SET updated_at = datetime('now') WHERE id = NEW.id;
END;
