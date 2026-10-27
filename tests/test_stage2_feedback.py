"""2026-10-27 user feedback: personal clipboard, categories and account safety."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from bson import ObjectId
import pytest

from app import create_app
from test_system import Actor, question
from test_stage2 import app, draft, save, use, grant


def ids(actor, scope="all", bank=None):
    suffix = f"&bankId={bank}" if bank else ""
    return [t["_id"] for t in actor.ok("GET", f"/api/templates?scope={scope}{suffix}")["templates"]]


def test_default_bank_unique_reserved_and_user_scoped(app):
    owner, other = Actor(app), Actor(app)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: Actor(app, token=owner.token).ok("GET", "/api/banks")["banks"], range(3)))
    assert len({rows[0]["_id"] for rows in results}) == 1
    default = results[0][0]
    assert len(results[0]) == 1 and default["name"] == "默认题库" and default["isDefault"]
    assert other.ok("GET", "/api/banks")["banks"][0]["_id"] != default["_id"]
    assert owner.request("DELETE", f"/api/banks/{default['_id']}").status_code == 400
    assert owner.request("PUT", f"/api/banks/{default['_id']}", {"name": "改名"}).status_code == 400
    for name in ("默认题库", "全部题库", "   "):
        assert owner.request("POST", "/api/banks", {"name": name}).status_code == 400


def test_multi_category_atomic_save_inline_create_and_idempotency(app):
    owner = Actor(app)
    default = owner.ok("GET", "/api/banks")["banks"][0]
    existing = owner.ok("POST", "/api/banks", {"name": "已有分类"})
    s = draft(owner)
    data = {"surveyId": s["_id"], "questionId": s["questions"][0]["_id"], "revision": s["revision"],
            "requestId": str(ObjectId()), "bankIds": [default["_id"], existing["_id"], default["_id"]], "newBanks": ["新建分类"]}
    result = owner.ok("POST", "/api/templates", data)
    tid = result["template"]["_id"]
    assert owner.ok("POST", "/api/templates", data)["template"]["_id"] == tid
    banks = owner.ok("GET", "/api/banks")["banks"]
    assert len(banks) == 3
    assert ids(owner) == [tid]
    assert set(result["template"]["bankIds"]) == {b["_id"] for b in banks}
    for b in banks:
        assert ids(owner, bank=b["_id"]) == [tid]


def test_unchecked_default_stays_unchecked_and_new_bank_failure_rolls_back(app):
    owner = Actor(app)
    s = draft(owner)
    t = save(owner, s, bankIds=[])
    default = owner.ok("GET", "/api/banks")["banks"][0]
    assert ids(owner, bank=default["_id"]) == []
    assert ids(owner) == [t["_id"]]
    s = draft(owner)
    data = {"requestId": str(ObjectId()), "surveyId": s["_id"], "questionId": s["questions"][0]["_id"],
            "revision": s["revision"], "bankIds": [str(ObjectId())], "newBanks": ["应回滚题库"]}
    assert owner.request("POST", "/api/templates", data).status_code == 404
    assert ids(owner) == [t["_id"]]
    assert not any(b["name"] == "应回滚题库" for b in owner.ok("GET", "/api/banks")["banks"])
    assert owner.ok("GET", f"/api/surveys/{s['_id']}")["revision"] == s["revision"]


def test_personal_remove_is_local_for_author_and_each_recipient(app):
    owner, recipient, third = Actor(app), Actor(app), Actor(app)
    t = save(owner, draft(owner))
    tid = t["_id"]
    grant(owner, tid, recipient)
    grant(owner, tid, third)
    bank = recipient.ok("POST", "/api/banks", {"name": "个人收藏"})
    recipient.ok("POST", f"/api/banks/{bank['_id']}/items", {"templateId": tid})
    kept = use(recipient, tid, draft(recipient))
    recipient.ok("DELETE", f"/api/templates/{tid}")
    assert tid not in ids(recipient) and tid not in ids(recipient, bank=bank["_id"])
    assert tid in ids(owner) and tid in ids(third)
    assert tid in ids(recipient, "history")
    assert recipient.ok("GET", f"/api/templates/{tid}")["question"] == t["question"]
    assert recipient.request("POST", f"/api/templates/{tid}/use", {"surveyId": kept["_id"], "revision": kept["revision"]}).status_code == 409
    recipient.ok("POST", f"/api/templates/{tid}/restore", {})
    assert tid in ids(recipient, bank=bank["_id"])
    owner.ok("DELETE", f"/api/templates/{tid}")
    assert tid not in ids(owner) and tid in ids(recipient) and tid in ids(third)
    assert recipient.ok("GET", f"/api/surveys/{kept['_id']}")["questions"][-1]["title"] == t["question"]["title"]
    db = app.extensions["db"]
    assert db.question_shares.count_documents({"templateId": ObjectId(tid), "active": True}) == 2
    assert not db.question_templates.find_one({"_id": ObjectId(tid)})["deleted"]
    fourth = Actor(app)
    grant(owner, tid, fourth)
    assert tid in ids(fourth) and tid not in ids(owner)
    owner.ok("DELETE", f"/api/templates/{tid}/shares/{recipient.user['_id']}")
    assert tid not in ids(recipient) and tid in ids(third)


def test_regrant_does_not_undo_recipient_remove_and_revoked_history_is_redacted(app):
    owner, recipient = Actor(app), Actor(app)
    t = save(owner, draft(owner))
    tid = t["_id"]
    grant(owner, tid, recipient)
    recipient.ok("DELETE", f"/api/templates/{tid}")
    grant(owner, tid, recipient)
    assert tid not in ids(recipient)
    owner.ok("DELETE", f"/api/templates/{tid}/shares/{recipient.user['_id']}")
    row = recipient.ok("GET", "/api/templates?scope=history")["templates"][0]
    assert row["readable"] is False and "question" not in row and "共享权限" in row["accessReason"]
    assert recipient.request("POST", f"/api/templates/{tid}/restore", {}).status_code == 404


def test_history_explains_permission_and_own_removed_node_readable(app):
    owner, recipient = Actor(app), Actor(app)
    t = save(owner, draft(owner))
    grant(owner, t["_id"], recipient)
    s = use(recipient, t["_id"], draft(recipient))
    s["questions"][-1]["title"] = "派生题目"
    s = recipient.ok("PUT", f"/api/surveys/{s['_id']}", s)
    child = save(recipient, s, -1)
    owner.ok("DELETE", f"/api/templates/{t['_id']}")
    own = owner.ok("GET", f"/api/templates/{t['_id']}/history")["nodes"][0]
    assert own["readable"] and own["deleted"] and own["canRestore"]
    owner.ok("DELETE", f"/api/templates/{t['_id']}/shares/{recipient.user['_id']}")
    parent = next(n for n in recipient.ok("GET", f"/api/templates/{child['_id']}/history")["nodes"] if n["_id"] == t["_id"])
    assert not parent["readable"] and "未共享" in parent["accessReason"] and "question" not in parent


def test_batch_classification_rejects_duplicate_without_partial_creations(app):
    owner = Actor(app)
    a, b = save(owner, draft(owner)), save(owner, draft(owner))
    bank = owner.ok("POST", "/api/banks", {"name": "已有同内容"})
    owner.ok("POST", f"/api/banks/{bank['_id']}/items", {"templateId": a["_id"]})
    response = owner.request("POST", f"/api/templates/{b['_id']}/banks", {"bankIds": [bank["_id"]], "newBanks": ["不能残留"]})
    assert response.status_code == 409
    assert not any(row["name"] == "不能残留" for row in owner.ok("GET", "/api/banks")["banks"])
    assert ids(owner, bank=bank["_id"]) == [a["_id"]]


def test_account_changed_header_blocks_wrong_identity_before_mutation(app):
    a, b = Actor(app), Actor(app)
    before = app.extensions["db"].surveys.count_documents({"creatorId": ObjectId(b.user["_id"])})
    response = b.client.post("/api/surveys", json={}, headers={"X-CSRF-Token": b.csrf, "X-Survey-User": a.user["_id"]})
    assert response.status_code == 409 and response.get_json()["code"] == "account_changed"
    assert response.headers["X-Survey-User"] == b.user["_id"]
    assert app.extensions["db"].surveys.count_documents({"creatorId": ObjectId(b.user["_id"])}) == before


def test_migration_preserves_legacy_removed_content_and_does_not_regrant(app):
    owner, recipient = Actor(app), Actor(app)
    t = save(owner, draft(owner), bankIds=[])
    grant(owner, t["_id"], recipient)
    db = app.extensions["db"]
    db.question_templates.update_one({"_id": ObjectId(t["_id"])}, {"$set": {"deleted": True}})
    db.question_shares.update_one({"templateId": ObjectId(t["_id"])}, {"$set": {"active": False}})
    db.template_entries.delete_many({"templateId": ObjectId(t["_id"])})
    db.template_state.update_one({"_id": "catalogue"}, {"$unset": {"personalEntriesVersion": ""}})
    upgraded = create_app({"TESTING": True, "MONGO_DB": db.name})
    try:
        assert t["_id"] in ids(owner, "history") and t["_id"] not in ids(owner)
        assert owner.ok("GET", f"/api/templates/{t['_id']}")["question"] == t["question"]
        assert recipient.request("GET", f"/api/templates/{t['_id']}").status_code == 404
        assert not db.question_templates.find_one({"_id": ObjectId(t["_id"])})["deleted"]
    finally:
        upgraded.extensions["mongo_client"].close()


def test_existing_default_named_bank_is_adopted_without_duplicate(app):
    owner = Actor(app)
    db = app.extensions["db"]
    bid = db.question_banks.insert_one({"ownerId": ObjectId(owner.user["_id"]), "name": "默认题库",
        "description": "已有分类", "deleted": False}).inserted_id
    banks = owner.ok("GET", "/api/banks")["banks"]
    assert len(banks) == 1 and banks[0]["_id"] == str(bid) and banks[0]["isDefault"]


def test_default_backfill_is_once_and_keeps_legacy_custom_classification(app):
    owner = Actor(app)
    db = app.extensions["db"]
    t = save(owner, draft(owner), bankIds=[])
    # Simulate a legacy unclassified entry, then exercise the upgrade read path.
    db.template_entries.update_one({"templateId": ObjectId(t["_id"])}, {"$unset": {"classificationChosen": ""}})
    banks = owner.ok("GET", "/api/banks")["banks"]
    bid = banks[0]["_id"]
    assert ids(owner, bank=bid) == [t["_id"]]
    owner.ok("DELETE", f"/api/banks/{bid}/items/{t['_id']}")
    owner.ok("GET", "/api/banks")
    assert ids(owner, bank=bid) == []


@pytest.mark.parametrize("name", [None, 12, [], {}, "题" * 101])
def test_bank_creation_rejects_invalid_name_without_leaving_record(app, name):
    owner = Actor(app)
    db = app.extensions["db"]
    before = db.question_banks.count_documents({"ownerId": ObjectId(owner.user["_id"])})
    assert owner.request("POST", "/api/banks", {"name": name}).status_code == 400
    assert db.question_banks.count_documents({"ownerId": ObjectId(owner.user["_id"])}) == before


def test_bank_names_trimmed_and_unique_only_within_current_user(app):
    owner, other = Actor(app), Actor(app)
    first = owner.ok("POST", "/api/banks", {"name": "  中文分类  "})
    assert first["name"] == "中文分类"
    assert owner.request("POST", "/api/banks", {"name": " 中文分类 "}).status_code == 409
    second = other.ok("POST", "/api/banks", {"name": "中文分类"})
    assert first["_id"] != second["_id"]
    own = owner.ok("GET", "/api/banks")["banks"]
    assert [b["_id"] for b in own if not b.get("isDefault")] == [first["_id"]]


def test_separate_bank_creation_survives_failed_template_save_then_multi_select(app):
    owner = Actor(app)
    default = owner.ok("GET", "/api/banks")["banks"][0]
    banks = [owner.ok("POST", "/api/banks", {"name": name}) for name in ("先建题库甲", "先建题库乙")]
    s = draft(owner)
    data = {"requestId": str(ObjectId()), "surveyId": s["_id"], "questionId": s["questions"][0]["_id"],
            "revision": s["revision"], "bankIds": [b["_id"] for b in banks]}
    assert owner.request("POST", "/api/templates", {**data, "bankIds": [str(ObjectId())]}).status_code == 404
    listed = {b["_id"] for b in owner.ok("GET", "/api/banks")["banks"]}
    assert all(b["_id"] in listed for b in banks)
    assert ids(owner) == []
    t = owner.ok("POST", "/api/templates", data)["template"]
    assert ids(owner) == [t["_id"]]
    for b in banks:
        assert ids(owner, bank=b["_id"]) == [t["_id"]]
    assert ids(owner, bank=default["_id"]) == []


def test_selected_bank_deleted_before_save_fails_without_partial_template(app):
    owner = Actor(app)
    bank = owner.ok("POST", "/api/banks", {"name": "已选择后被删除"})
    s = draft(owner)
    owner.ok("DELETE", f"/api/banks/{bank['_id']}")
    data = {"requestId": str(ObjectId()), "surveyId": s["_id"], "questionId": s["questions"][0]["_id"],
            "revision": s["revision"], "bankIds": [bank["_id"]]}
    assert owner.request("POST", "/api/templates", data).status_code == 404
    assert ids(owner) == []
    db = app.extensions["db"]
    assert db.template_entries.count_documents({"userId": ObjectId(owner.user["_id"])}) == 0
    assert db.template_sources.count_documents({"surveyId": ObjectId(s["_id"])}) == 0
    assert owner.ok("GET", f"/api/surveys/{s['_id']}")["revision"] == s["revision"]


def test_concurrent_same_bank_name_creates_only_one_record(app):
    owner = Actor(app)
    barrier = Barrier(2)
    def create(_):
        actor = Actor(app, token=owner.token)
        barrier.wait(timeout=10)
        return actor.request("POST", "/api/banks", {"name": "并发同名新题库"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, range(2))) == [201, 409]
    db = app.extensions["db"]
    assert db.question_banks.count_documents({"ownerId": ObjectId(owner.user["_id"]), "name": "并发同名新题库"}) == 1
