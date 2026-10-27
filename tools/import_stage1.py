"""Copy a read-only stage-one snapshot into an empty, isolated stage-two database."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import BSON
from pymongo import MongoClient
from pymongo.read_concern import ReadConcern

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db import initialize_database

COLLECTIONS = ("users", "surveys", "survey_versions", "responses")


def digest(rows):
    return hashlib.sha256(b"".join(BSON.encode(row) for row in sorted(rows, key=lambda r: str(r["_id"])))).hexdigest()


def main():
    source = MongoClient("mongodb://127.0.0.1:27018/?replicaSet=survey-rs", serverSelectionTimeoutMS=5000)
    target = MongoClient("mongodb://127.0.0.1:27019/?replicaSet=survey-stage2-rs", serverSelectionTimeoutMS=5000)
    src, dst = source["survey_coursework"], target["survey_coursework_stage2"]
    if any(dst[c].count_documents({}) for c in COLLECTIONS) or dst.question_templates.count_documents({}):
        raise SystemExit("第二阶段数据库已有业务数据，拒绝覆盖；本工具仅用于首次导入。")
    initialize_database(dst)
    with source.start_session() as session:
        with session.start_transaction(read_concern=ReadConcern("snapshot")):
            snapshot = {c: list(src[c].find({}, session=session)) for c in COLLECTIONS}
    with target.start_session() as session:
        def insert(s):
            for name, rows in snapshot.items():
                if rows:
                    dst[name].insert_many(rows, session=s)
        session.with_transaction(insert)
    record = {"createdAt": datetime.now(timezone.utc).isoformat(), "source": "27018/survey_coursework (read only)",
              "target": "27019/survey_coursework_stage2", "collections": {}}
    for name, rows in snapshot.items():
        target_rows = list(dst[name].find({}))
        assert digest(rows) == digest(target_rows), name
        record["collections"][name] = {"count": len(rows), "sha256": digest(rows)}
    output = ROOT.parent / "文档" / "旧数据首次导入记录.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v["count"] for k, v in record["collections"].items()}))
    source.close(); target.close()


if __name__ == "__main__":
    main()
