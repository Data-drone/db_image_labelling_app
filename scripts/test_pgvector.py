"""
Test pgvector support on Lakebase.

Connects to the existing cv-explorer Lakebase project (or creates it),
installs the vector extension, and runs through vector operations:
  1. CREATE EXTENSION vector
  2. Create a table with a vector column
  3. Insert sample vectors
  4. Run distance queries (L2, cosine, inner product)
  5. Create HNSW and IVFFlat indexes
  6. Query using the indexes
  7. Clean up
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from backend.lakebase import init_lakebase

TEST_TABLE = "pgvector_test"


def main():
    print("=" * 60)
    print("pgvector on Lakebase — End-to-End Test")
    print("=" * 60)

    # ── Connect ────────────────────────────────────────────────
    print("\n[1/7] Connecting to Lakebase...")
    engine = init_lakebase()
    print("  OK — connected")

    with engine.connect() as conn:
        # ── Install extension ──────────────────────────────────
        print("\n[2/7] Installing pgvector extension...")
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

        row = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        print(f"  OK — vector extension v{row[0]} installed")

        # ── Create table ───────────────────────────────────────
        print(f"\n[3/7] Creating test table '{TEST_TABLE}'...")
        conn.execute(text(f"DROP TABLE IF EXISTS {TEST_TABLE}"))
        conn.execute(text(f"""
            CREATE TABLE {TEST_TABLE} (
                id   SERIAL PRIMARY KEY,
                label TEXT NOT NULL,
                embedding vector(3)
            )
        """))
        conn.commit()
        print("  OK — table created with vector(3) column")

        # ── Insert vectors ─────────────────────────────────────
        print("\n[4/7] Inserting sample vectors...")
        samples = [
            ("cat",    "[1, 0, 0]"),
            ("dog",    "[0.9, 0.1, 0]"),
            ("fish",   "[0, 0, 1]"),
            ("bird",   "[0.1, 0.8, 0.1]"),
            ("lizard", "[0.3, 0.3, 0.7]"),
        ]
        for label, vec in samples:
            conn.execute(
                text(f"INSERT INTO {TEST_TABLE} (label, embedding) VALUES (:label, :vec)"),
                {"label": label, "vec": vec},
            )
        conn.commit()
        print(f"  OK — inserted {len(samples)} rows")

        # ── Distance queries ───────────────────────────────────
        print("\n[5/7] Running distance queries...")

        query_vec = "[1, 0, 0]"  # should be closest to "cat"

        # L2 distance (<->)
        print("\n  L2 distance (closest to [1,0,0]):")
        rows = conn.execute(text(f"""
            SELECT label, embedding, embedding <-> :q AS distance
            FROM {TEST_TABLE}
            ORDER BY embedding <-> :q
            LIMIT 5
        """), {"q": query_vec}).fetchall()
        for r in rows:
            print(f"    {r.label:8s}  {str(r.embedding):20s}  L2={r.distance:.4f}")

        # Cosine distance (<=>)
        print("\n  Cosine distance (closest to [1,0,0]):")
        rows = conn.execute(text(f"""
            SELECT label, embedding, embedding <=> :q AS distance
            FROM {TEST_TABLE}
            ORDER BY embedding <=> :q
            LIMIT 5
        """), {"q": query_vec}).fetchall()
        for r in rows:
            print(f"    {r.label:8s}  {str(r.embedding):20s}  cos={r.distance:.4f}")

        # Inner product (<#>)
        print("\n  Negative inner product (closest to [1,0,0]):")
        rows = conn.execute(text(f"""
            SELECT label, embedding, embedding <#> :q AS distance
            FROM {TEST_TABLE}
            ORDER BY embedding <#> :q
            LIMIT 5
        """), {"q": query_vec}).fetchall()
        for r in rows:
            print(f"    {r.label:8s}  {str(r.embedding):20s}  nip={r.distance:.4f}")

        # ── HNSW index ─────────────────────────────────────────
        print("\n[6/7] Creating indexes...")

        print("  Creating HNSW index (cosine)...")
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_hnsw_cosine
            ON {TEST_TABLE}
            USING hnsw (embedding vector_cosine_ops)
        """))
        conn.commit()
        print("  OK — HNSW index created")

        print("  Creating IVFFlat index (L2)...")
        conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS idx_ivfflat_l2
            ON {TEST_TABLE}
            USING ivfflat (embedding vector_l2_ops)
            WITH (lists = 2)
        """))
        conn.commit()
        print("  OK — IVFFlat index created")

        # Verify indexes are used
        print("\n  Checking EXPLAIN for HNSW cosine query...")
        plan = conn.execute(text(f"""
            EXPLAIN SELECT label FROM {TEST_TABLE}
            ORDER BY embedding <=> :q LIMIT 3
        """), {"q": query_vec}).fetchall()
        plan_text = "\n".join(r[0] for r in plan)
        print(f"  Plan:\n    " + plan_text.replace("\n", "\n    "))
        if "hnsw" in plan_text.lower():
            print("  OK — HNSW index is being used")
        else:
            print("  NOTE — planner chose seq scan (expected for small tables)")

        # ── Cleanup ────────────────────────────────────────────
        print(f"\n[7/7] Cleaning up test table '{TEST_TABLE}'...")
        conn.execute(text(f"DROP TABLE IF EXISTS {TEST_TABLE}"))
        conn.commit()
        print("  OK — table dropped")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED — pgvector works on Lakebase!")
    print("=" * 60)


if __name__ == "__main__":
    main()
