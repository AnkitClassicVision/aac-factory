#!/usr/bin/env python3
"""
Synthesize a Concept Flow Map (Justin Sung river map) from an ATLAS territory map.

Reads  <package>/atlas/atlas.json
Writes <package>/<slug>_concept_map/knowledge/patches/NNN_atlas_synthesis.sql (regeneration patch)
       <package>/atlas/crosswalk.json (ATLAS node <-> concept node traceability)
Then applies the patch, runs lint, and exports JSON + Mermaid via the package's own scripts.

Mapping rules (deterministic):
  - ATLAS spine_path        -> trunk chain (rel_precedes, is_trunk=1), root -> spine[0]
  - ATLAS root_anchors      -> rel_part_of children of root (spine claim synthesized if none)
  - ATLAS clusters/members  -> branches under their trunk node (chunked 2-3, max 3, bridge concepts when >3)
  - ATLAS edge types        -> serves->enables, prereq->requires, part-of->part_of, causes->causes,
                               feeds->triggers, differentiated-by->is_a
  - shaky / tension edges   -> never concept edges; surfaced as flags + validation backlog
  - confidence/provenance/status/atlas id -> node tags; proof_slot -> support concept when needed

The generator REFUSES (with precise fixes) rather than emit a map that would fail lint.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

EDGE_TYPE_MAP = {
    "serves": "rel_enables",
    "prereq": "rel_requires",
    "part-of": "rel_part_of",
    "causes": "rel_causes",
    "feeds": "rel_triggers",
    "differentiated-by": "rel_is_a",
}
SKIP_EDGE_TYPES = {"shaky", "tension"}


def sql_quote(s: str) -> str:
    return "'" + (s or "").replace("'", "''") + "'"


def snake(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.strip().lower())
    return re.sub(r"_+", "_", s).strip("_") or "concept"


def node_db_id(label: str) -> str:
    h = hashlib.sha1(label.encode("utf-8")).hexdigest()[:6]
    return f"node_{snake(label)}_{h}"


def edge_db_id(from_id: str, to_id: str) -> str:
    h = hashlib.sha1(f"{from_id}->{to_id}".encode("utf-8")).hexdigest()[:6]
    return f"edge_{h}_{snake(from_id)[-20:]}__{snake(to_id)[-20:]}"


def chunk_sizes(n: int) -> list[int]:
    """Split n into chunks of 3 and 2 — never a chunk of 1."""
    if n <= 3:
        return [n]
    full, rem = divmod(n, 3)
    if rem == 0:
        return [3] * full
    if rem == 1:
        return [3] * (full - 1) + [2, 2]
    return [3] * full + [2]


class Synthesis:
    def __init__(self, atlas: dict, topic: str):
        self.atlas = atlas
        self.topic = topic
        self.by_id = {n["id"]: n for n in atlas.get("nodes", [])}
        self.concept_nodes: dict[str, dict] = {}   # db_id -> node row
        self.edges: list[dict] = []                # edge rows
        self.crosswalk: dict[str, dict] = {}       # atlas id -> mapping
        self.synthesized: list[dict] = []
        self.flags: list[str] = []
        self.errors: list[str] = []
        self.labels: set[str] = set()
        self.outgoing: dict[str, int] = {}
        self.non_trunk_out: dict[str, int] = {}
        self.crosslink_degree: dict[str, int] = {}

    # ---------- node helpers ----------

    def unique_label(self, name: str, grouping: str) -> str:
        label = name.strip()
        if label in self.labels:
            label = f"{name.strip()} ({grouping.strip()})" if grouping else f"{name.strip()} (2)"
        if label in self.labels:
            self.errors.append(f"Duplicate concept label after dedupe: {name!r} — rename one ATLAS node.")
        self.labels.add(label)
        return label

    def add_concept(self, atlas_node: dict, role: str, cluster: str | None = None) -> str:
        aid = atlas_node["id"]
        if aid in self.crosswalk:
            return self.crosswalk[aid]["concept_node_id"]
        label = self.unique_label(atlas_node["name"], atlas_node.get("grouping", ""))
        db_id = node_db_id(label)
        conf = (atlas_node.get("confidence") or "Hunch")[:1].upper()
        tags = (
            f"atlas:{aid},conf:{conf},prov:{atlas_node.get('provenance', 'user')},"
            f"status:{atlas_node.get('status', 'open')}"
        )
        proof = atlas_node.get("proof_slot") or ""
        self.concept_nodes[db_id] = {
            "id": db_id,
            "label": label,
            "short_def": atlas_node.get("short_def") or atlas_node["name"],
            "why_important": atlas_node.get("why_important")
            or (f"Proven when: {proof}" if proof else "Unproven (Hunch) — needs a proof slot."),
            "deep_dive": "",
            "example": "",
            "eli5": atlas_node.get("eli5") or "",
            "tags": tags,
        }
        self.crosswalk[aid] = {
            "concept_node_id": db_id,
            "concept_label": label,
            "role": role,
            "cluster": cluster,
        }
        return db_id

    def add_synth_node(self, label: str, short_def: str, why: str, tags: str, kind: str) -> str:
        label = self.unique_label(label, "")
        db_id = node_db_id(label)
        self.concept_nodes[db_id] = {
            "id": db_id, "label": label, "short_def": short_def, "why_important": why,
            "deep_dive": "", "example": "", "eli5": "", "tags": tags,
        }
        self.synthesized.append({"concept_node_id": db_id, "label": label, "kind": kind})
        return db_id

    # ---------- edge helpers ----------

    def add_edge(self, frm: str, to: str, rel: str, *, trunk: bool, why: str, how: str,
                 order: int | None = None) -> None:
        self.edges.append({
            "id": edge_db_id(frm, to), "from": frm, "to": to, "rel": rel,
            "is_trunk": 1 if trunk else 0, "order": order, "why": why, "how": how,
        })
        self.outgoing[frm] = self.outgoing.get(frm, 0) + 1
        if not trunk:
            self.non_trunk_out[frm] = self.non_trunk_out.get(frm, 0) + 1
            if rel != "rel_part_of":
                for end in (frm, to):
                    self.crosslink_degree[end] = self.crosslink_degree.get(end, 0) + 1

    def atlas_edge_between(self, a: str, b: str) -> dict | None:
        for e in self.atlas.get("edges", []):
            if e.get("from") == a and e.get("to") == b and e.get("type") not in SKIP_EDGE_TYPES:
                return e
        return None

    # ---------- build ----------

    def build(self) -> None:
        atlas = self.atlas
        spine_path = atlas.get("spine_path") or []
        spine = (atlas.get("spine") or "").strip()

        if not spine or spine.startswith("TODO"):
            self.errors.append("SPINE is empty/TODO. Lock one falsifiable claim before synthesis.")
        if not 3 <= len(spine_path) <= 9:
            self.errors.append(f"spine_path needs 3-9 node ids (got {len(spine_path)}).")
        missing = [i for i in spine_path if i not in self.by_id]
        if missing:
            self.errors.append(f"spine_path ids missing from nodes: {missing}")
        if self.errors:
            return

        clusters = atlas.get("clusters", [])
        cluster_by_trunk = {}
        for c in clusters:
            if c.get("trunk_node") in cluster_by_trunk:
                self.errors.append(f"Two clusters share trunk_node {c.get('trunk_node')}.")
            cluster_by_trunk[c.get("trunk_node")] = c

        used_as_member: set[str] = set()
        for c in clusters:
            for m in c.get("members", []):
                if m in spine_path:
                    self.errors.append(f"Node {m} is both spine and cluster member — pick one role.")
                if m in used_as_member:
                    self.errors.append(f"Node {m} appears in two clusters — single structural parent required.")
                if m not in self.by_id:
                    self.errors.append(f"Cluster member {m} missing from nodes.")
                used_as_member.add(m)
        if self.errors:
            return

        # Root + anchors
        root_id = "node_root"
        self.labels.add(f"{self.topic} — Concept Flow")
        anchors = atlas.get("root_anchors") or []
        anchor_ids: list[str] = []
        for a in anchors[:2]:
            if a not in self.by_id:
                self.errors.append(f"root_anchor {a} missing from nodes.")
                continue
            if a in spine_path or a in used_as_member:
                self.errors.append(f"root_anchor {a} already used as spine/member.")
                continue
            anchor_ids.append(self.add_concept(self.by_id[a], "anchor"))
        if not anchor_ids:
            anchor_ids.append(self.add_synth_node(
                "Spine — Falsifiable Win Claim",
                spine,
                "Every layer of this package must serve this claim; if it breaks, re-map.",
                "atlas:spine,conf:R,prov:user,status:open,synth", "spine_anchor",
            ))

        # Trunk chain
        spine_db: list[str] = [self.add_concept(self.by_id[i], "spine") for i in spine_path]
        first = spine_db[0]
        self.add_edge(root_id, first, "rel_precedes", trunk=True, order=0,
                      why="Entry into the sense-making chain for this agent.",
                      how="ATLAS spine_path order (root -> first trunk concept).")
        for adb in anchor_ids:
            self.add_edge(root_id, adb, "rel_part_of", trunk=False,
                          why="Always-visible anchor: keeps the map tied to the work object and the win.",
                          how="ATLAS root_anchors convention (work object + win condition).")
        for i in range(len(spine_db) - 1):
            a_atlas, b_atlas = spine_path[i], spine_path[i + 1]
            ae = self.atlas_edge_between(a_atlas, b_atlas)
            why = (ae or {}).get("reason") or (
                f"Logic chain: {self.by_id[a_atlas]['name']} sets up {self.by_id[b_atlas]['name']}."
            )
            how = f"ATLAS spine order {i + 1}->{i + 2}" + (
                f"; edge {ae['id']} ({ae['type']}, conf {ae.get('confidence', '?')})" if ae else ""
            )
            self.add_edge(spine_db[i], spine_db[i + 1], "rel_precedes", trunk=True, order=i + 1,
                          why=why, how=how)

        # Clusters -> branches
        for idx, atlas_id in enumerate(spine_path):
            trunk_db = spine_db[idx]
            cluster = cluster_by_trunk.get(atlas_id)
            members = list(cluster.get("members", [])) if cluster else []
            cname = cluster.get("name") if cluster else self.by_id[atlas_id]["name"]
            cpurpose = (cluster.get("purpose") if cluster else "") or f"Supports {cname}."
            terminal = idx == len(spine_path) - 1
            anode = self.by_id[atlas_id]
            proof = (anode.get("proof_slot") or "").strip()

            def attach(parent_db: str, member_atlas_ids: list[str]) -> None:
                for order, mid in enumerate(member_atlas_ids):
                    m = self.by_id[mid]
                    mdb = self.add_concept(m, "member", cname)
                    ae = self.atlas_edge_between(atlas_id, mid) or self.atlas_edge_between(mid, atlas_id)
                    rel = EDGE_TYPE_MAP.get((ae or {}).get("type", ""), "rel_part_of")
                    if rel == "rel_part_of" or ae is None:
                        rel, frm, to = "rel_part_of", parent_db, mdb
                        why = (ae or {}).get("reason") or f"Member of '{cname}': {cpurpose}"
                    else:
                        frm, to = (trunk_db, mdb) if ae.get("from") == atlas_id else (mdb, trunk_db)
                        if frm != parent_db:
                            # keep tree shape: structural attach + note the direction in why
                            rel, frm, to = "rel_part_of", parent_db, mdb
                        why = ae.get("reason") or f"Member of '{cname}': {cpurpose}"
                    how = (
                        f"ATLAS {m.get('provenance', 'user')}/{m.get('confidence', 'Hunch')}; "
                        f"proof: {m.get('proof_slot') or 'none yet (Hunch)'}"
                    )
                    # branch_order namespace: trunk edges use 0-9, branches use 10+ (lint: unique per parent)
                    self.add_edge(frm, to, rel, trunk=False, order=10 + order, why=why, how=how)

            if len(members) == 0:
                if terminal:
                    continue
                if proof:
                    pdb = self.add_synth_node(
                        f"{anode['name']} — proof signal", proof,
                        f"Makes the proof slot for '{anode['name']}' a visible concept.",
                        f"atlas:{atlas_id}.proof,conf:R,prov:inferred-model,status:open,synth", "proof_slot",
                    )
                    sdb = self.add_synth_node(
                        f"{anode['name']} — open questions",
                        f"Validation backlog items for {anode['name']}.",
                        "A trunk concept with no supports yet — fill from the next interview round.",
                        f"atlas:{atlas_id}.open,conf:H,prov:inferred-model,status:open,synth", "open_questions",
                    )
                    for n, w in ((pdb, "Surfaces what would prove this trunk concept."),
                                 (sdb, "Holds the unanswered structure under this trunk concept.")):
                        self.add_edge(trunk_db, n, "rel_measured_by" if n == pdb else "rel_part_of",
                                      trunk=False, why=w,
                                      how="Synthesized from ATLAS proof_slot (no members in cluster).")
                    self.flags.append(f"{atlas_id}: trunk concept had no members — proof-slot supports synthesized.")
                else:
                    self.errors.append(
                        f"{atlas_id} ('{anode['name']}'): non-terminal trunk concept with no cluster members "
                        f"and no proof_slot. Fix atlas.json: add >=2 members or a proof_slot."
                    )
                continue

            if len(members) == 1:
                if proof:
                    attach(trunk_db, members)
                    pdb = self.add_synth_node(
                        f"{anode['name']} — proof signal", proof,
                        f"Sibling for the single member; makes the proof slot visible.",
                        f"atlas:{atlas_id}.proof,conf:R,prov:inferred-model,status:open,synth", "proof_slot",
                    )
                    self.add_edge(trunk_db, pdb, "rel_measured_by", trunk=False,
                                  why="Surfaces what would prove this trunk concept.",
                                  how="Synthesized from ATLAS proof_slot (single-member cluster).")
                    self.flags.append(f"{atlas_id}: single-member cluster — proof-slot sibling synthesized.")
                else:
                    self.errors.append(
                        f"{atlas_id} ('{anode['name']}'): cluster has exactly 1 member and no proof_slot. "
                        f"Fix atlas.json: add a sibling member, merge the member, or add a proof_slot."
                    )
                continue

            sizes = chunk_sizes(len(members))
            if len(sizes) == 1:
                attach(trunk_db, members)
            else:
                if len(sizes) > 3:
                    self.errors.append(
                        f"{atlas_id} ('{anode['name']}'): {len(members)} members needs {len(sizes)} bridges (>3). "
                        f"Split the cluster in atlas.json."
                    )
                    continue
                pos = 0
                for bi, size in enumerate(sizes, start=1):
                    chunk = members[pos:pos + size]
                    pos += size
                    groupings = {self.by_id[m].get("grouping", "") for m in chunk}
                    g = groupings.pop() if len(groupings) == 1 else ""
                    blabel = f"{cname}: {g}" if g and g != cname else f"{cname} (part {bi})"
                    bdb = self.add_synth_node(
                        blabel, f"Bridge grouping inside '{cname}'.",
                        f"Keeps branches chunked (2-3) instead of a hairball: {cpurpose}",
                        f"atlas:{atlas_id}.bridge{bi},conf:R,prov:inferred-model,status:open,synth,bridge",
                        "bridge",
                    )
                    self.add_edge(trunk_db, bdb, "rel_part_of", trunk=False, order=10 + bi,
                                  why=f"Chunking bridge for '{cname}' ({size} members).",
                                  how="Deterministic 2-3 chunking of ATLAS cluster members.")
                    attach(bdb, chunk)

        # Crosslinks from remaining ATLAS edges
        for e in self.atlas.get("edges", []):
            et = e.get("type")
            if et in SKIP_EDGE_TYPES:
                self.flags.append(
                    f"ATLAS edge {e.get('id')} ({et}) {e.get('from')}->{e.get('to')}: "
                    f"needs human audit/evidence — {e.get('reason', '')}"
                )
                continue
            a, b = e.get("from"), e.get("to")
            if a not in self.crosswalk or b not in self.crosswalk:
                continue
            fdb, tdb = self.crosswalk[a]["concept_node_id"], self.crosswalk[b]["concept_node_id"]
            if any(x["from"] == fdb and x["to"] == tdb for x in self.edges):
                continue
            rel = EDGE_TYPE_MAP.get(et)
            if rel is None or rel == "rel_part_of":
                continue
            if self.outgoing.get(fdb, 0) < 2:
                self.flags.append(
                    f"Crosslink skipped (would create a single-child node): {e.get('id')} "
                    f"{a}->{b} ({et}). Insert a bridge concept if it matters."
                )
                continue
            if self.outgoing.get(fdb, 0) >= 4 or self.non_trunk_out.get(fdb, 0) >= 3:
                self.flags.append(f"Crosslink skipped (children full at {a}): {e.get('id')} {a}->{b} ({et}).")
                continue
            if self.crosslink_degree.get(fdb, 0) >= 3 or self.crosslink_degree.get(tdb, 0) >= 3:
                self.flags.append(f"Crosslink skipped (hairball guard): {e.get('id')} {a}->{b} ({et}).")
                continue
            self.add_edge(fdb, tdb, rel, trunk=False,
                          why=e.get("reason") or f"ATLAS crosslink ({et}).",
                          how=f"ATLAS edge {e.get('id')} ({et}, conf {e.get('confidence', '?')}).")

        # Final single-child sanity (defense in depth)
        for nid, count in self.outgoing.items():
            if count == 1 and nid != "node_root":
                self.errors.append(f"Internal: node {nid} would end with exactly 1 child — report this case.")
        if self.outgoing.get("node_root", 0) == 1:
            self.errors.append("Internal: root would end with exactly 1 child.")

    # ---------- emit ----------

    def to_sql(self) -> str:
        lines = [
            "PRAGMA foreign_keys = ON;",
            "",
            f"-- Concept Flow Map synthesized from ATLAS: {self.atlas.get('atlas_id', '?')}",
            f"-- SPINE: {self.atlas.get('spine', '')[:160]}",
            "-- Regeneration patch: wipes non-root content, then rebuilds from atlas.json.",
            "",
            "BEGIN;",
            "DELETE FROM edge WHERE map_id = 'default';",
            "DELETE FROM node WHERE map_id = 'default' AND id != 'node_root';",
            f"UPDATE node SET label = {sql_quote(self.topic + ' — Concept Flow')}, "
            f"short_def = {sql_quote('Sense-making root for: ' + self.topic)} WHERE id = 'node_root';",
            "",
        ]
        for n in self.concept_nodes.values():
            lines.append(
                "INSERT INTO node (id, map_id, label, short_def, why_important, deep_dive, example, eli5, tags)\n"
                f"VALUES ({sql_quote(n['id'])}, 'default', {sql_quote(n['label'])}, {sql_quote(n['short_def'])}, "
                f"{sql_quote(n['why_important'])}, {sql_quote(n['deep_dive'])}, {sql_quote(n['example'])}, "
                f"{sql_quote(n['eli5'])}, {sql_quote(n['tags'])});"
            )
        lines.append("")
        for e in self.edges:
            order = "NULL" if e["order"] is None else str(e["order"])
            lines.append(
                "INSERT INTO edge (id, map_id, from_node_id, to_node_id, rel_type_id, is_trunk, branch_order, why, how)\n"
                f"VALUES ({sql_quote(e['id'])}, 'default', {sql_quote(e['from'])}, {sql_quote(e['to'])}, "
                f"{sql_quote(e['rel'])}, {e['is_trunk']}, {order}, {sql_quote(e['why'])}, {sql_quote(e['how'])});"
            )
        lines.append("COMMIT;")
        return "\n".join(lines) + "\n"


def run(cmd: list[str], cwd: Path) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        raise SystemExit(f"Command failed ({' '.join(cmd)}) in {cwd}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/atlas_to_concept.py <package_dir>")
    pkg = Path(sys.argv[1]).resolve()
    atlas_path = pkg / "atlas" / "atlas.json"
    if not atlas_path.exists():
        raise SystemExit(f"Missing {atlas_path}")
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    topic = (pkg / "CONCEPT.txt").read_text(encoding="utf-8").strip() if (pkg / "CONCEPT.txt").exists() else atlas.get("subject", pkg.name)

    map_dirs = sorted(pkg.glob("*_concept_map"))
    if not map_dirs:
        raise SystemExit(f"No *_concept_map folder in {pkg}")
    map_dir = map_dirs[0]

    syn = Synthesis(atlas, topic)
    syn.build()
    if syn.errors:
        print("REFUSED — fix atlas.json first:")
        for err in syn.errors:
            print(f"  - {err}")
        raise SystemExit(2)

    patches = map_dir / "knowledge" / "patches"
    patches.mkdir(parents=True, exist_ok=True)
    existing = [p for p in patches.glob("[0-9][0-9][0-9]_*.sql")]
    nnn = max([int(p.name[:3]) for p in existing], default=0) + 1
    patch_path = patches / f"{nnn:03d}_atlas_synthesis.sql"
    patch_path.write_text(syn.to_sql(), encoding="utf-8")
    print("Wrote patch:", patch_path)

    crosswalk = {
        "atlas_id": atlas.get("atlas_id"),
        "spine": atlas.get("spine"),
        "generated_by": "atlas_to_concept.py",
        "atlas_nodes": syn.crosswalk,
        "synthesized_concepts": syn.synthesized,
        "flags": syn.flags,
        "process_nodes": {},
    }
    cw_path = pkg / "atlas" / "crosswalk.json"
    if cw_path.exists():
        try:
            old = json.loads(cw_path.read_text(encoding="utf-8"))
            crosswalk["process_nodes"] = old.get("process_nodes", {})
        except Exception:
            pass
    cw_path.write_text(json.dumps(crosswalk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Wrote crosswalk:", cw_path)

    py = sys.executable
    run([py, "scripts/apply_sql.py", str(patch_path.relative_to(map_dir))], map_dir)
    run([py, "scripts/lint_db.py"], map_dir)
    run([py, "scripts/export_map.py", "default"], map_dir)

    cs_path = pkg / "CONTROL_STATE.json"
    if cs_path.exists():
        cs = json.loads(cs_path.read_text(encoding="utf-8"))
        cs.setdefault("atlas", {})["status"] = "SYNTHESIZED"
        cs["atlas"]["spine_locked"] = True
        cs.setdefault("concept_map", {})["status"] = "LINT_PASS_EXPORTED"
        cs.setdefault("crosswalk", {})["status"] = "GENERATED"
        flags = [f for f in cs.get("flags", []) if not str(f).startswith("[atlas_to_concept]")]
        flags += [f"[atlas_to_concept] {f}" for f in syn.flags]
        cs["flags"] = flags
        cs_path.write_text(json.dumps(cs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if syn.flags:
        print(f"\nFlags ({len(syn.flags)}):")
        for f in syn.flags:
            print(f"  - {f}")
    print("\n[OK] Concept map synthesized, lint clean, exports written.")


if __name__ == "__main__":
    main()
