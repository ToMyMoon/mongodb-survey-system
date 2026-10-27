"""Back up stage-two catalogue before upgrade; never connects to stage one."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from bson import BSON
from pymongo import MongoClient
from pymongo.read_concern import ReadConcern

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = ("question_templates", "question_shares", "question_banks",
               "question_bank_items", "template_entries", "template_sources", "template_state")


def business_digest(db, session=None):
    result = {}
    for name in ("surveys", "survey_versions", "responses"):
        digest, count = hashlib.sha256(), 0
        for doc in db[name].find({}, session=session).sort("_id", 1):
            digest.update(BSON.encode(doc))
            count += 1
        result[name] = {"count": count, "sha256": digest.hexdigest()}
    return result


def main():
    client = MongoClient("mongodb://127.0.0.1:27019/?replicaSet=survey-stage2-rs", serverSelectionTimeoutMS=5000)
    options = client.admin.command("getCmdLineOpts")["parsed"]
    assert client.admin.command("hello")["setName"] == "survey-stage2-rs"
    assert Path(options["storage"]["dbPath"]).resolve() == (ROOT / ".mongo").resolve()
    db = client.survey_coursework_stage2
    directory = ROOT / ".mongo" / "catalogue-backups"
    directory.mkdir(exist_ok=True)
    target = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bson")
    counts = {}
    with client.start_session() as session:
        with session.start_transaction(read_concern=ReadConcern("snapshot")):
            with target.open("xb") as stream:
                for name in COLLECTIONS:
                    counts[name] = 0
                    for doc in db[name].find({}, session=session):
                        stream.write(BSON.encode({"collection": name, "document": doc}))
                        counts[name] += 1
            business = business_digest(db, session)
    metadata = {"backup": str(target), "counts": counts, "business": business,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    target.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))
    client.close()


if __name__ == "__main__":
    main()
