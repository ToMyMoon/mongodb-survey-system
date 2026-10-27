"""Stage-two integration: real MongoDB, provenance, permissions and aggregates."""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from bson import ObjectId

from test_system import app, Actor, question, published, payload
from template_library import signature


def draft(owner, questions=None):
    survey = owner.ok("POST", "/api/surveys", {})
    survey.update(title="第二阶段测试", questions=questions or [question("number", minValue="0", maxValue="120")])
    return owner.ok("PUT", f"/api/surveys/{survey['_id']}", survey)


def save(owner, survey, index=0, **extra):
    data = {"requestId": str(ObjectId()), "surveyId": survey["_id"], "questionId": survey["questions"][index]["_id"], "revision": survey["revision"], **extra}
    return owner.ok("POST", "/api/templates", data)["template"]


def use(owner, tid, survey):
    owner.ok("POST", f"/api/templates/{tid}/use", {"surveyId": survey["_id"], "revision": survey["revision"]})
    return owner.ok("GET", f"/api/surveys/{survey['_id']}")


def grant(owner, tid, other):
    owner.ok("POST", f"/api/templates/{tid}/shares", {"username": other.user["username"], "userId": other.user["_id"]})


def publish(owner, survey):
    return owner.ok("POST", f"/api/surveys/{survey['_id']}/publish", {"revision": survey["revision"]})


def test_template_snapshot_partial_draft_and_idempotent(app):
    owner = Actor(app)
    q, unfinished = question("number", minValue="0"), question()
    unfinished["title"] = ""
    survey = draft(owner, [q, unfinished])
    data = {"requestId": str(ObjectId()), "surveyId": survey["_id"], "questionId": q["_id"], "revision": survey["revision"]}
    first = owner.ok("POST", "/api/templates", data)
    assert owner.ok("POST", "/api/templates", data)["template"]["_id"] == first["template"]["_id"]
    tid = first["template"]["_id"]
    assert "jumpRules" not in first["template"]["question"]
    changed = owner.ok("GET", f"/api/surveys/{survey['_id']}")
    changed["questions"][0]["title"] = "新题干"
    owner.ok("PUT", f"/api/surveys/{survey['_id']}", changed)
    assert owner.ok("GET", f"/api/templates/{tid}")["question"]["title"] == q["title"]
    assert owner.request("PUT", f"/api/templates/{tid}", {"question": q}).status_code == 405
    assert owner.request("POST", "/api/templates", {**data, "questionId": unfinished["_id"], "requestId": str(ObjectId()), "revision": changed["revision"]+1}).status_code == 400


def test_invalid_jumps_do_not_prevent_single_template(app):
    owner = Actor(app)
    q = question()
    q["jumpRules"] = [{"targetQuestionId": str(ObjectId()), "condition": {"operator": "eq", "value": q["options"][0]["_id"]}}]
    t = save(owner, draft(owner, [q]))
    assert "jumpRules" not in t["question"]


def test_use_duplicates_and_provenance_tampering(app):
    owner, other = Actor(app), Actor(app)
    t = save(owner, draft(owner, [question()]))
    dest = draft(owner)
    dest = use(owner, t["_id"], dest)
    dest = use(owner, t["_id"], dest)
    a, b = dest["questions"][-2:]
    assert a["_id"] != b["_id"] and a["options"][0]["_id"] != b["options"][0]["_id"]
    a["title"] = "修改副本"
    dest = owner.ok("PUT", f"/api/surveys/{dest['_id']}", dest)
    assert owner.ok("GET", f"/api/templates/{t['_id']}")["question"]["title"] != a["title"]
    forged = draft(other)
    forged["questions"][0]["sourceTemplateId"] = t["_id"]
    assert other.request("PUT", f"/api/surveys/{forged['_id']}", forged).status_code == 400
    assert other.request("POST", f"/api/templates/{t['_id']}/use", {"surveyId": forged["_id"], "revision": forged["revision"]}).status_code == 404


def test_share_revoke_derived_and_history_access(app):
    owner, other, third = Actor(app), Actor(app), Actor(app)
    t = save(owner, draft(owner))
    grant(owner, t["_id"], other)
    grant(owner, t["_id"], other)
    assert "source" not in other.ok("GET", f"/api/templates/{t['_id']}")
    assert len(owner.ok("GET", f"/api/templates/{t['_id']}/shares")["users"]) == 1
    assert other.request("POST", f"/api/templates/{t['_id']}/shares", {"username": third.user["username"], "userId": third.user["_id"]}).status_code == 404
    dest = use(other, t["_id"], draft(other))
    data = {"requestId": str(ObjectId()), "surveyId": dest["_id"], "questionId": dest["questions"][-1]["_id"], "revision": dest["revision"]}
    assert other.request("POST", "/api/templates", data).get_json()["code"] == "unchanged_template"
    dest["questions"][-1]["title"] = "自己的新模板"
    dest = other.ok("PUT", f"/api/surveys/{dest['_id']}", dest)
    child = save(other, dest, -1)
    assert child["sourceTemplateId"] == t["_id"]
    grant(other, child["_id"], third)
    owner.ok("DELETE", f"/api/templates/{t['_id']}/shares/{other.user['_id']}")
    assert other.request("GET", f"/api/templates/{t['_id']}").status_code == 404
    nodes = other.ok("GET", f"/api/templates/{child['_id']}/history")["nodes"]
    parent = next(n for n in nodes if n["_id"] == t["_id"])
    assert not parent["readable"] and "question" not in parent
    assert third.ok("GET", f"/api/templates/{child['_id']}")["question"]["title"] == "自己的新模板"
    kept = other.ok("GET", f"/api/surveys/{dest['_id']}")
    assert publish(other, kept)


def test_bank_remove_restore_conflict_keeps_other_users_grants(app):
    owner, other = Actor(app), Actor(app)
    t = save(owner, draft(owner))
    b = owner.ok("POST", "/api/banks", {"name": "信息", "description": ""})
    path = f"/api/banks/{b['_id']}/items"
    owner.ok("POST", path, {"templateId": t["_id"]})
    owner.ok("POST", path, {"templateId": t["_id"]})
    grant(owner, t["_id"], other)
    owner.ok("DELETE", f"/api/templates/{t['_id']}")
    t2 = save(owner, draft(owner))
    owner.ok("POST", path, {"templateId": t2["_id"]})
    restored = owner.ok("POST", f"/api/templates/{t['_id']}/restore", {})
    assert set(restored["skippedBanks"]) == {"默认题库", "信息"}
    assert len(owner.ok("GET", f"/api/templates?bankId={b['_id']}")["templates"]) == 1
    assert other.ok("GET", f"/api/templates/{t['_id']}")["question"] == t["question"]
    assert owner.request("POST", path, {"templateId": t["_id"]}).get_json()["code"] == "duplicate_content"
    owner.ok("DELETE", f"/api/banks/{b['_id']}")
    assert owner.ok("GET", f"/api/templates/{t['_id']}")


def test_bank_permissions_and_shared_classification(app):
    owner, other = Actor(app), Actor(app)
    t = save(owner, draft(owner))
    grant(owner, t["_id"], other)
    b = other.ok("POST", "/api/banks", {"name": "收藏"})
    other.ok("POST", f"/api/banks/{b['_id']}/items", {"templateId": t["_id"]})
    assert len(other.ok("GET", f"/api/templates?scope=all&bankId={b['_id']}")["templates"]) == 1
    assert owner.request("DELETE", f"/api/banks/{b['_id']}").status_code == 404
    owner.ok("DELETE", f"/api/templates/{t['_id']}/shares/{other.user['_id']}")
    assert other.ok("GET", f"/api/templates?scope=all&bankId={b['_id']}")["templates"] == []


@pytest.mark.parametrize("kind", ["single", "multiple", "text", "number"])
def test_cross_survey_counts_and_owner_isolation(app, kind):
    owner, other, filler = Actor(app), Actor(app), Actor(app)
    source = draft(owner, [question(kind)])
    t = save(owner, source)
    for actor, answer in [(owner, "20"), (owner, "22"), (other, "100")]:
        if actor is other:
            grant(owner, t["_id"], other)
        s = use(actor, t["_id"], draft(actor, [question("text", required=False)]))
        # Repeat the template in each survey; every instance counts independently.
        s = use(actor, t["_id"], s)
        v = publish(actor, s)
        values = {}
        for q in v["questions"][1:]:
            values[q["_id"]] = q["options"][0]["_id"] if kind == "single" else [q["options"][0]["_id"]] if kind == "multiple" else answer
        filler.ok("POST", f"/api/fill/{s['_id']}/submit", payload(v, values))
    stats = owner.ok("GET", f"/api/templates/{t['_id']}/stats")
    assert stats["answerCount"] == 4 and stats["respondentCount"] == 1
    if kind == "single":
        assert stats["options"][0]["count"] == 1 and stats["options"][1]["count"] == 0
    if kind == "multiple":
        assert stats["options"][0]["count"] == 4
    if kind == "number":
        assert float(stats["average"]) == 21
        assert stats["minimum"] == "20" and stats["maximum"] == "22"
        assert [r["count"] for r in stats["distribution"]] == [2, 2]
    assert other.ok("GET", f"/api/templates/{t['_id']}/stats")["answerCount"] == 2
    assert owner.ok("GET", f"/api/templates/{t['_id']}/usage")["surveyCount"] == 3  # source plus 2 own destinations


def test_legacy_mapping_immutable_versions_and_filters(app):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner, [question("number")])
    before = copy.deepcopy(app.extensions["db"].survey_versions.find_one({"_id": ObjectId(version["_id"])}))
    filler.ok("POST", f"/api/fill/{sid}/submit", payload(version, {version["questions"][0]["_id"]: "20"}))
    survey = owner.ok("GET", f"/api/surveys/{sid}")
    t = save(owner, survey, versionId=version["_id"])
    assert app.extensions["db"].survey_versions.find_one({"_id": ObjectId(version["_id"])}) == before
    assert owner.ok("GET", f"/api/templates/{t['_id']}/stats")["answerCount"] == 1
    assert owner.ok("GET", f"/api/templates/{t['_id']}/stats?from=2099-01-01T00:00:00Z")["answerCount"] == 0
    other = Actor(app)
    assert other.request("GET", f"/api/templates/{t['_id']}/stats").status_code == 404
    assert owner.ok("GET", f"/api/templates/{t['_id']}/stats?surveyId={sid}&versionId={version['_id']}")["answerCount"] == 1


def test_signature_normalization_and_required_rule():
    q = question("number", minValue="1.0")
    other = copy.deepcopy(q)
    other["validation"]["minValue"] = "1"
    assert signature(q) == signature(other)
    other["required"] = not q["required"]
    assert signature(q) != signature(other)
    assert signature(q, True) == signature(other, True)
    other["title"] += " "
    assert signature(q, True) != signature(other, True)


def test_transaction_rollback_on_duplicate_bank(app):
    owner = Actor(app)
    b = owner.ok("POST", "/api/banks", {"name": "唯一"})
    s = draft(owner)
    t = save(owner, s, bankId=b["_id"])
    s2 = draft(owner)
    data = {"requestId": str(ObjectId()), "surveyId": s2["_id"], "questionId": s2["questions"][0]["_id"], "revision": s2["revision"], "bankId": b["_id"]}
    assert owner.request("POST", "/api/templates", data).status_code == 409
    db = app.extensions["db"]
    assert db.question_templates.count_documents({"ownerId": ObjectId(owner.user["_id"])}) == 1
    assert db.template_sources.count_documents({"surveyId": ObjectId(s2["_id"])}) == 0
    assert owner.ok("GET", f"/api/surveys/{s2['_id']}")["revision"] == s2["revision"]


def test_concurrent_same_template_save_is_idempotent(app):
    owner = Actor(app)
    s = draft(owner)
    data = {"requestId": str(ObjectId()), "surveyId": s["_id"], "questionId": s["questions"][0]["_id"], "revision": s["revision"]}
    barrier = Barrier(4)
    def send(_):
        actor = Actor(app, token=owner.token)
        barrier.wait(timeout=10)
        return actor.ok("POST", "/api/templates", data)["template"]["_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(send, range(4)))
    assert len(set(ids)) == 1


def test_multiple_releases_modified_then_restored_content(app):
    owner, filler = Actor(app), Actor(app)
    s = draft(owner)
    s['allowMultipleSubmissions'] = True
    s = owner.ok('PUT', f"/api/surveys/{s['_id']}", s)
    t = save(owner, s)
    s = owner.ok('GET', f"/api/surveys/{s['_id']}")
    original_title = s['questions'][0]['title']
    snapshots = []
    for title, value in [(original_title, '20'), ('不同含义', '90'), (original_title, '30')]:
        s['questions'][0]['title'] = title
        if value == '30':
            s['questions'][0]['required'] = False
        s = owner.ok('PUT', f"/api/surveys/{s['_id']}", s)
        v = publish(owner, s)
        snapshots.append(v)
        filler.ok('POST', f"/api/fill/{s['_id']}/submit", payload(v, {v['questions'][0]['_id']: value}))
        current = owner.ok('GET', f"/api/surveys/{s['_id']}")
        owner.ok('POST', f"/api/surveys/{s['_id']}/close", {'revision': current['revision']})
        s = owner.ok('GET', f"/api/surveys/{s['_id']}")
    stats = owner.ok('GET', f"/api/templates/{t['_id']}/stats")
    assert stats['answerCount'] == 2 and stats['respondentCount'] == 1
    assert float(stats['average']) == 25
    assert len(stats['sources']) == 2
    assert owner.ok('GET', f"/api/templates/{t['_id']}/stats?versionId={snapshots[1]['_id']}")['answerCount'] == 0
    owner.ok('DELETE', f"/api/templates/{t['_id']}")
    assert owner.ok('GET', f"/api/templates/{t['_id']}/stats")['answerCount'] == 2


def test_draft_incomplete_numeric_rule_does_not_break_usage(app):
    owner = Actor(app)
    s = draft(owner)
    t = save(owner, s)
    s = owner.ok('GET', f"/api/surveys/{s['_id']}")
    s['questions'][0]['validation']['minValue'] = '-'
    owner.ok('PUT', f"/api/surveys/{s['_id']}", s)
    usage = owner.ok('GET', f"/api/templates/{t['_id']}/usage")
    assert usage['surveys'][0]['instances'][0]['modified']
    assert owner.ok('GET', f"/api/templates/{t['_id']}/stats")['average'] is None


def test_concurrent_bank_content_uniqueness(app):
    owner = Actor(app)
    a, b = save(owner, draft(owner)), save(owner, draft(owner))
    bank = owner.ok('POST', '/api/banks', {'name': '并发唯一'})
    barrier = Barrier(2)
    def add(t):
        actor = Actor(app, token=owner.token)
        barrier.wait(timeout=10)
        return actor.request('POST', f"/api/banks/{bank['_id']}/items", {'templateId': t['_id']}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(add, [a, b])) == [200, 409]


def test_concurrent_revoke_and_use_never_enables_future_use(app):
    owner, other = Actor(app), Actor(app)
    t = save(owner, draft(owner))
    grant(owner, t['_id'], other)
    s = draft(other)
    barrier = Barrier(2)
    def revoke():
        actor = Actor(app, token=owner.token)
        barrier.wait(timeout=10)
        return actor.request('DELETE', f"/api/templates/{t['_id']}/shares/{other.user['_id']}").status_code
    def consume():
        actor = Actor(app, token=other.token)
        barrier.wait(timeout=10)
        return actor.request('POST', f"/api/templates/{t['_id']}/use", {'surveyId': s['_id'], 'revision': s['revision']}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        r, u = pool.submit(revoke), pool.submit(consume)
        assert r.result() == 200
        assert u.result() in (201, 404)
    current = other.ok('GET', f"/api/surveys/{s['_id']}")
    assert other.request('POST', f"/api/templates/{t['_id']}/use", {'surveyId': s['_id'], 'revision': current['revision']}).status_code == 404


def test_bank_required_is_content_but_stats_ignore_required(app):
    owner = Actor(app)
    a = save(owner, draft(owner, [question('number', required=True)]))
    b = save(owner, draft(owner, [question('number', required=False)]))
    bank = owner.ok('POST', '/api/banks', {'name': '不同必答'})
    for t in [a,b]:
        owner.ok('POST', f"/api/banks/{bank['_id']}/items", {'templateId': t['_id']})
    assert len(owner.ok('GET', f"/api/templates?bankId={bank['_id']}")['templates']) == 2


def test_own_history_branches_restore_keeps_identity(app):
    owner = Actor(app)
    a = save(owner, draft(owner))
    children = []
    for title in ['分支一', '分支二']:
        s = use(owner, a['_id'], draft(owner))
        s['questions'][-1]['title'] = title
        s = owner.ok('PUT', f"/api/surveys/{s['_id']}", s)
        children.append(save(owner, s, -1))
    owner.ok('DELETE', f"/api/templates/{a['_id']}")
    nodes = owner.ok('GET', f"/api/templates/{children[0]['_id']}/history")['nodes']
    assert {n['_id'] for n in nodes} == {a['_id'], children[0]['_id'], children[1]['_id']}
    owner.ok('POST', f"/api/templates/{a['_id']}/restore", {})
    assert not owner.ok('GET', f"/api/templates/{a['_id']}")['deleted']


def test_published_source_retains_parent_when_closed_and_resaved(app):
    owner = Actor(app)
    sid, version = published(owner, [question('number')])
    survey = owner.ok('GET', f'/api/surveys/{sid}')
    parent = save(owner, survey, versionId=version['_id'])
    current = owner.ok('GET', f'/api/surveys/{sid}')
    owner.ok('POST', f'/api/surveys/{sid}/close', {'revision': current['revision']})
    current = owner.ok('GET', f'/api/surveys/{sid}')
    current['questions'][0]['title'] = '关闭后派生'
    current = owner.ok('PUT', f'/api/surveys/{sid}', current)
    child = save(owner, current)
    assert child['sourceTemplateId'] == parent['_id']
    original = app.extensions['db'].survey_versions.find_one({'_id': ObjectId(version['_id'])})
    assert 'sourceTemplateId' not in original['questions'][0]
