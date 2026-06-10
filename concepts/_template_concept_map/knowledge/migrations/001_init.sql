PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS map (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  description     TEXT,
  root_node_id    TEXT,
  max_depth       INTEGER NOT NULL DEFAULT 5,
  max_children    INTEGER NOT NULL DEFAULT 4,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(root_node_id) REFERENCES node(id)
);

CREATE TABLE IF NOT EXISTS node (
  id              TEXT PRIMARY KEY,
  map_id          TEXT NOT NULL,
  label           TEXT NOT NULL,
  short_def       TEXT,
  why_important   TEXT,
  deep_dive       TEXT,
  example         TEXT,
  eli5            TEXT,
  tags            TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(map_id) REFERENCES map(id) ON DELETE CASCADE
);

-- No duplicate concepts by label within a map.
CREATE UNIQUE INDEX IF NOT EXISTS uq_node_label_per_map ON node(map_id, label);

CREATE TABLE IF NOT EXISTS rel_type (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  description     TEXT
);

CREATE TABLE IF NOT EXISTS edge (
  id              TEXT PRIMARY KEY,
  map_id          TEXT NOT NULL,
  from_node_id    TEXT NOT NULL,
  to_node_id      TEXT NOT NULL,
  rel_type_id     TEXT NOT NULL,
  is_trunk        INTEGER NOT NULL DEFAULT 0,
  branch_order    INTEGER,
  depth_hint      INTEGER,
  why             TEXT NOT NULL DEFAULT '',
  how             TEXT NOT NULL DEFAULT '',
  strength        INTEGER,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(map_id) REFERENCES map(id) ON DELETE CASCADE,
  FOREIGN KEY(from_node_id) REFERENCES node(id) ON DELETE CASCADE,
  FOREIGN KEY(to_node_id) REFERENCES node(id) ON DELETE CASCADE,
  FOREIGN KEY(rel_type_id) REFERENCES rel_type(id)
);

CREATE INDEX IF NOT EXISTS idx_edge_from ON edge(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_to   ON edge(to_node_id);
CREATE INDEX IF NOT EXISTS idx_edge_map  ON edge(map_id);

CREATE INDEX IF NOT EXISTS idx_edge_trunk_from ON edge(map_id, from_node_id, is_trunk);
