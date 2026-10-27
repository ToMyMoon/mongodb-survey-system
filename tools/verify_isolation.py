"""Read-only verification of stage-one files and copied business documents."""
import hashlib
import json
import sys
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.import_stage1 import digest


def main():
    docs = ROOT.parent / "文档"
    first = ROOT.parents[1] / "第一阶段"
    manifest = json.loads((docs / "第一阶段基线校验.json").read_text(encoding="utf-8-sig"))
    changed = [r["path"] for r in manifest if hashlib.sha256((first / r["path"]).read_bytes()).hexdigest().upper() != r["sha256"]]
    assert not changed, changed
    imported = json.loads((docs / "旧数据首次导入记录.json").read_text(encoding="utf-8-sig"))
    source = MongoClient("mongodb://127.0.0.1:27018/?replicaSet=survey-rs", serverSelectionTimeoutMS=5000)
    target = MongoClient("mongodb://127.0.0.1:27019/?replicaSet=survey-stage2-rs", serverSelectionTimeoutMS=5000)
    counts = {}
    for name, expected in imported["collections"].items():
        original = list(source.survey_coursework[name].find({}))
        assert digest(original) == expected["sha256"], f"source changed: {name}"
        copied = list(target.survey_coursework_stage2[name].find({"_id": {"$in": [r["_id"] for r in original]}}))
        assert digest(copied) == expected["sha256"], f"imported records changed: {name}"
        counts[name] = len(original)
    result = {"checkedFiles": len(manifest), "changedFiles": changed, "businessCollectionsUnchanged": counts}
    (docs / "第一阶段隔离复核.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    source.close(); target.close()


if __name__ == "__main__":
    main()
