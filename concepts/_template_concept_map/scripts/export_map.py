import json, sqlite3, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "..", "knowledge", "knowledge.db")
OUT_JSON = os.path.join(ROOT, "..", "knowledge", "exports", "latest.rivermap.json")
OUT_MMD  = os.path.join(ROOT, "..", "knowledge", "exports", "latest.rivermap.mmd")

def fetchall(conn, q, params=()):
    cur = conn.execute(q, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

def safe(s):
    return (s or "").replace('"', '\"')

def main(map_id="default"):
    conn = sqlite3.connect(DB)
    m = fetchall(conn, "SELECT * FROM map WHERE id = ?", (map_id,))
    if not m:
        raise SystemExit(f"map_id not found: {map_id}")
    m = m[0]

    nodes = fetchall(conn, "SELECT * FROM node WHERE map_id = ? ORDER BY label", (map_id,))
    edges = fetchall(conn, '''
        SELECT e.*, rt.name AS rel_name
        FROM edge e
        JOIN rel_type rt ON rt.id = e.rel_type_id
        WHERE e.map_id = ?
        ORDER BY e.is_trunk DESC, e.branch_order ASC
    ''', (map_id,))

    doc = {
        "map": {
            "id": m["id"],
            "name": m["name"],
            "description": m["description"],
            "max_depth": m["max_depth"],
            "max_children": m["max_children"],
        },
        "root": m["root_node_id"],
        "nodes": [
            {k: n.get(k) for k in ["id","label","short_def","why_important","deep_dive","example","eli5","tags"]}
            for n in nodes
        ],
        "edges": [
            {
                "id": e["id"],
                "from": e["from_node_id"],
                "to": e["to_node_id"],
                "type": e["rel_name"],
                "is_trunk": bool(e["is_trunk"]),
                "order": e["branch_order"],
                "depth_hint": e["depth_hint"],
                "why": e.get("why"),
                "how": e.get("how"),
                "strength": e.get("strength"),
                "notes": e["notes"],
            } for e in edges
        ],
        "rules": {
            "max_children_per_node": m["max_children"],
            "recommended_children_per_node": [2, 3],
            "no_single_child_rule": True,
            "max_depth": m["max_depth"],
        }
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)

    # Mermaid
    labels = {n["id"]: n["label"] for n in nodes}
    lines = ["flowchart LR"]
    for n in nodes:
        lines.append(f'  {n["id"]}["{safe(n["label"])}"]')
    trunk_nodes = set()
    for e in edges:
        lines.append(f'  {e["from_node_id"]} -- "{safe(e["rel_name"])}" --> {e["to_node_id"]}')
        if e["is_trunk"]:
            trunk_nodes.add(e["from_node_id"]); trunk_nodes.add(e["to_node_id"])
    if trunk_nodes:
        lines.append("  classDef trunk stroke-width:3px;")
        lines.append("  class " + ",".join(sorted(trunk_nodes)) + " trunk;")
    with open(OUT_MMD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("Wrote:", OUT_JSON)
    print("Wrote:", OUT_MMD)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "default")
