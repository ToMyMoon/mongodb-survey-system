"""Regression cases found by the 2026-10-26 requirements audit."""
from decimal import Decimal

import pytest
from bson import ObjectId

from test_system import Actor, question, payload
from test_stage2 import app, draft, save, use, grant, publish


def test_successive_saves_keep_immediate_parent_and_branch(app):
    owner = Actor(app)
    original = draft(owner)
    a = save(owner, original)
    current = owner.ok("GET", f"/api/surveys/{original['_id']}")
    children = []
    for title in ("修改一次", "修改两次", original["questions"][0]["title"]):
        current["questions"][0]["title"] = title
        current = owner.ok("PUT", f"/api/surveys/{current['_id']}", current)
        children.append(save(owner, current))
        current = owner.ok("GET", f"/api/surveys/{current['_id']}")
    assert children[0]["sourceTemplateId"] == a["_id"]
    assert children[1]["sourceTemplateId"] == children[0]["_id"]
    assert children[2]["sourceTemplateId"] == children[1]["_id"]
    assert children[2]["_id"] != a["_id"]
    branch = use(owner, a["_id"], draft(owner))
    branch["questions"][-1]["title"] = "从最初版本派生"
    branch = owner.ok("PUT", f"/api/surveys/{branch['_id']}", branch)
    assert save(owner, branch, -1)["sourceTemplateId"] == a["_id"]


def test_null_saved_template_ids_cannot_poison_usage(app):
    owner = Actor(app)
    survey = draft(owner)
    survey["questions"][0]["savedTemplateIds"] = None
    result = owner.request("PUT", f"/api/surveys/{survey['_id']}", survey)
    assert result.status_code == 400
    stored = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    assert "savedTemplateIds" not in stored["questions"][0]


def test_legacy_null_source_can_save_and_derive(app):
    owner = Actor(app)
    survey = draft(owner)
    survey["questions"][0]["sourceTemplateId"] = None
    survey = owner.ok("PUT", f"/api/surveys/{survey['_id']}", survey)
    parent = save(owner, survey)
    current = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    assert current["questions"][0]["sourceTemplateId"] == parent["_id"]
    current["questions"][0]["title"] = "修改后的空来源题"
    current = owner.ok("PUT", f"/api/surveys/{survey['_id']}", current)
    assert save(owner, current)["sourceTemplateId"] == parent["_id"]


@pytest.mark.parametrize("operation", ["save", "use"])
@pytest.mark.parametrize("bad_revision", [True, 1.0])
def test_catalogue_operations_require_integer_revision(app, operation, bad_revision):
    owner = Actor(app)
    t = save(owner, draft(owner))
    target = draft(owner)
    data = {"surveyId": target["_id"], "revision": bad_revision}
    if operation == "save":
        data.update(questionId=target["questions"][0]["_id"], requestId=str(ObjectId()))
        endpoint = "/api/templates"
    else:
        endpoint = f"/api/templates/{t['_id']}/use"
    assert owner.request("POST", endpoint, data).status_code in (400, 409)
    assert owner.ok("GET", f"/api/surveys/{target['_id']}")["revision"] == target["revision"]


def test_numeric_mean_keeps_small_values_between_large_opposites(app):
    owner, respondent = Actor(app), Actor(app)
    survey = draft(owner, [question("number")])
    survey["allowMultipleSubmissions"] = True
    survey = owner.ok("PUT", f"/api/surveys/{survey['_id']}", survey)
    template = save(owner, survey)
    survey = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    version = publish(owner, survey)
    for value in ("1" + "0" * 100, "1", "-1" + "0" * 100):
        respondent.ok("POST", f"/api/fill/{survey['_id']}/submit", payload(version, {version["questions"][0]["_id"]: value}))
    stats = owner.ok("GET", f"/api/templates/{template['_id']}/stats")
    assert stats["answerCount"] == 3
    assert Decimal("0.333333") < Decimal(stats["average"]) < Decimal("0.333334")


def test_shared_template_history_never_exposes_private_descendants(app):
    author, recipient = Actor(app), Actor(app)
    parent = save(author, draft(author))
    grant(author, parent["_id"], recipient)
    branch = use(author, parent["_id"], draft(author))
    branch["questions"][-1]["title"] = "未共享的私人分支"
    branch = author.ok("PUT", f"/api/surveys/{branch['_id']}", branch)
    child = save(author, branch, -1)
    nodes = recipient.ok("GET", f"/api/templates/{parent['_id']}/history")["nodes"]
    assert child["_id"] not in {node["_id"] for node in nodes}


def test_source_mapping_is_once_per_instance_and_never_retroactive(app):
    owner, respondent = Actor(app), Actor(app)
    survey = draft(owner, [question("number")])
    survey["allowMultipleSubmissions"] = True
    survey = owner.ok("PUT", f"/api/surveys/{survey['_id']}", survey)
    old = publish(owner, survey)
    respondent.ok("POST", f"/api/fill/{survey['_id']}/submit", payload(old, {old["questions"][0]["_id"]: "1"}))
    current = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    owner.ok("POST", f"/api/surveys/{survey['_id']}/close", {"revision": current["revision"]})
    current = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    template = save(owner, current)
    current = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    current = use(owner, template["_id"], current)
    version = publish(owner, current)
    respondent.ok("POST", f"/api/fill/{survey['_id']}/submit", payload(version, {q["_id"]: "2" for q in version["questions"]}))
    stats = owner.ok("GET", f"/api/templates/{template['_id']}/stats")
    assert stats["answerCount"] == 2  # direct binding and saved binding do not double count
    assert stats["respondentCount"] == 1
    assert all(source["versionId"] != old["_id"] for source in stats["sources"])


def test_template_is_restored_without_recreating_deleted_bank(app):
    owner = Actor(app)
    bank = owner.ok("POST", "/api/banks", {"name": "已删除分类"})
    template = save(owner, draft(owner), bankId=bank["_id"])
    owner.ok("DELETE", f"/api/templates/{template['_id']}")
    owner.ok("DELETE", f"/api/banks/{bank['_id']}")
    owner.ok("POST", f"/api/templates/{template['_id']}/restore", {})
    assert all(b.get("isDefault") for b in owner.ok("GET", "/api/banks")["banks"])
    assert not owner.ok("GET", f"/api/templates/{template['_id']}")["deleted"]
