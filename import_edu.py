"""Import the education sample workspace into a DataMind profile.

Mirrors scripts/import_wsb.py but adapted to this sample's layout:

    edu_sample/kb_docs/*.md              -> kb    (copied verbatim, DataMind indexes)
    edu_sample/tables/*.csv              -> db    (one SQLite table per CSV)
    edu_sample/graph/surface_graph.json  -> graph (node-link -> GraphTriple JSONL)

Two things are preserved deliberately:

1. **Table names** equal the CSV stem (`course_catalog`, `major_transfer_plan`),
   because the task gold derivations reference those names.
2. **Node ids** are the natural entity names (`数学与应用数学`, `PHY0191`), not
   synthetic ids — this is the whole point of the naming-hygiene rule: nothing
   in an identifier should encode which task it belongs to.

Node attributes (type/discipline/name) have nowhere to live on an edge-shaped
triple, so they are folded into each triple's `properties` as subject_meta /
object_meta, same as the WSB importer.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

PROFILE = "edu_registrar"


def convert_graph(graph_json: Path, out_jsonl: Path) -> dict[str, int]:
    graph = json.loads(graph_json.read_text(encoding="utf-8"))
    nodes = {str(n["id"]): n for n in graph.get("nodes", []) if n.get("id")}

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    with out_jsonl.open("w", encoding="utf-8") as fh:
        for edge in graph.get("edges", []):
            subject = edge.get("from")
            obj = edge.get("to")
            relation = edge.get("rel")
            if not (subject and obj and relation):
                skipped += 1
                continue
            sn = nodes.get(str(subject), {})
            on = nodes.get(str(obj), {})
            # Edge-level attributes (course_type / credits) matter for queries
            # like "required courses only", so keep them alongside the metas.
            extra = {
                k: v for k, v in edge.items()
                if k not in {"from", "to", "rel"}
            }
            triple = {
                "subject": str(subject),
                "relation": str(relation),
                "object": str(obj),
                "subject_type": sn.get("type", "entity"),
                "object_type": on.get("type", "entity"),
                "source": "edu-sample",
                "properties": {
                    "subject_meta": {k: v for k, v in sn.items() if k != "id"},
                    "object_meta": {k: v for k, v in on.items() if k != "id"},
                    **extra,
                },
            }
            fh.write(json.dumps(triple, ensure_ascii=False) + "\n")
            written += 1
    return {"nodes": len(nodes), "edges_written": written, "edges_skipped": skipped}


def import_tables(tables_dir: Path, db_path: Path) -> dict[str, Any]:
    import pandas as pd

    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    loaded: list[str] = []
    failures: list[dict[str, str]] = []
    total_rows = 0
    try:
        for csv in sorted(tables_dir.glob("*.csv")):
            table = csv.stem  # MUST match names used in task gold derivations
            try:
                frame = pd.read_csv(csv)
                frame.to_sql(table, connection, if_exists="replace", index=False)
                loaded.append(f"{table}({len(frame)})")
                total_rows += len(frame)
            except Exception as exc:  # noqa: BLE001 - report, don't abort batch
                failures.append({"table": table, "error": f"{type(exc).__name__}: {exc}"})
        connection.commit()
    finally:
        connection.close()
    return {"tables": loaded, "rows": total_rows, "failures": failures}


def import_kb(kb_dir: Path, dest_dir: Path) -> dict[str, int]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for doc in sorted(kb_dir.glob("*.md")):
        shutil.copy2(doc, dest_dir / doc.name)
        copied += 1
    return {"docs": copied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="edu_sample root")
    parser.add_argument("--datamind", required=True, help="DataMind repo root")
    parser.add_argument("--profile", default=PROFILE)
    args = parser.parse_args()

    src = Path(args.src)
    datamind_root = Path(args.datamind)
    data_dir = datamind_root / "data" / "profiles" / args.profile
    storage_dir = datamind_root / "storage" / args.profile

    # Idempotent: a re-run must not merge two imports.
    for stale in (data_dir, storage_dir):
        if stale.exists():
            shutil.rmtree(stale)
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    kb = import_kb(src / "kb_docs", data_dir / "kb_docs")
    graph = convert_graph(src / "graph" / "surface_graph.json",
                          data_dir / "triplets" / "surface_graph.jsonl")
    db = import_tables(src / "tables", storage_dir / "demo.db")

    print(f"profile: {args.profile}")
    print(f"  kb    : {kb['docs']} docs")
    print(f"  graph : {graph['nodes']} nodes, {graph['edges_written']} edges"
          f" (skipped {graph['edges_skipped']})")
    print(f"  db    : {', '.join(db['tables'])} = {db['rows']} rows")
    for f in db["failures"]:
        print(f"  !! table {f['table']}: {f['error'][:120]}")
    print()
    print("next: build the KB index")
    print(f"  DATAMIND__DATA__PROFILE={args.profile} python -m datamind ingest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
