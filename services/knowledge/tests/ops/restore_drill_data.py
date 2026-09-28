"""Synthetic SQL + real Chroma + files. No embeddings provider, auth or LLM."""
import sys
from pathlib import Path

import chromadb
import psycopg

client = chromadb.PersistentClient(path="/app/data/restore-drill")
collection = client.get_or_create_collection("restore-drill")
docs = Path("/app/docs")
with psycopg.connect("postgresql://drill:synthetic-only@db/drill") as db:
    if sys.argv[1] == "seed":
        db.execute("CREATE TABLE drill_records (id text PRIMARY KEY, value text NOT NULL)")
        db.execute("INSERT INTO drill_records VALUES ('account', 'synthetic@example.test'), ('document', 'known.txt'), ('deletion_job', 'pending')")
        (docs / "known.txt").write_text("Known product: synthetic 42", encoding="utf-8")
        collection.add(ids=["known"], embeddings=[[1.0, 0.0, 0.0]], documents=["Known product: synthetic 42"])
    elif sys.argv[1] == "mutate":
        db.execute("UPDATE drill_records SET value='changed' WHERE id='deletion_job'")
        db.execute("INSERT INTO drill_records VALUES ('new-document', 'after.txt')")
        (docs / "known.txt").unlink()
        (docs / "after.txt").write_text("must disappear", encoding="utf-8")
        collection.delete(ids=["known"])
        collection.add(ids=["after"], embeddings=[[0.0, 1.0, 0.0]], documents=["must disappear"])
    elif sys.argv[1] == "verify":
        assert dict(db.execute("SELECT id,value FROM drill_records").fetchall()) == {
            "account":"synthetic@example.test", "document":"known.txt", "deletion_job":"pending"}
        assert (docs / "known.txt").read_text(encoding="utf-8") == "Known product: synthetic 42"
        assert not (docs / "after.txt").exists()
        assert collection.get()["ids"] == ["known"]
        result = collection.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=1)
        assert result["documents"] == [["Known product: synthetic 42"]]
        print("PASS: SQL records + pending marker + files + Chroma query; post-snapshot data absent")
    else:
        raise ValueError("unknown drill action")
