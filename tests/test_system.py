"""Integration tests use real MongoDB transactions and indexes, never a mock DB."""
import copy
import secrets
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from threading import Barrier, Event

import pytest
from bson import ObjectId, Decimal128

from app import create_app, now
from engine import BusinessError, check_publish, evaluate, normalize_answer, number


def question(kind="single", required=True, **validation):
    return {"_id": str(ObjectId()), "type": kind, "title": "测试问题", "required": required,
            "options": [{"_id": str(ObjectId()), "text": "选项 A"}, {"_id": str(ObjectId()), "text": "选项 B"}] if kind in ("single", "multiple") else [],
            "validation": validation, "jumpRules": []}


@pytest.fixture(scope="session")
def app():
    name = "survey_test_" + secrets.token_hex(6)
    instance = create_app({"TESTING": True, "MONGO_DB": name})
    yield instance
    assert name.startswith("survey_test_")
    instance.extensions["mongo_client"].drop_database(name)
    instance.extensions["mongo_client"].close()


class Actor:
    def __init__(self, app, name=None, token=None):
        self.app = app
        self.client = app.test_client()
        if token:
            self.client.set_cookie("survey_session", token)
            data = self.client.get("/api/auth/me").get_json()
        else:
            data = self.client.post("/api/auth/register", json={"username": name or secrets.token_hex(6), "password": "password12"}).get_json()
        self.csrf, self.user = data["csrf"], data["user"]

    @property
    def token(self):
        return self.client.get_cookie("survey_session").value

    def request(self, method, path, data=None):
        return self.client.open(path, method=method, json=data, headers={"X-CSRF-Token": self.csrf})

    def ok(self, method, path, data=None):
        result = self.request(method, path, data)
        assert result.status_code < 300, result.get_json()
        return result.get_json()


def published(owner, questions=None, multiple=False, anonymous=False):
    survey = owner.ok("POST", "/api/surveys", {})
    sid = survey["_id"]
    survey.update(title="集成测试问卷", description="测试说明", questions=questions or [question()], allowMultipleSubmissions=multiple, allowAnonymous=anonymous)
    saved = owner.ok("PUT", f"/api/surveys/{sid}", survey)
    version = owner.ok("POST", f"/api/surveys/{sid}/publish", {"revision": saved["revision"]})
    return sid, version


def payload(version, values=None, anonymous=False, code=None):
    q = version["questions"][0]
    if values is None:
        values = {q["_id"]: q["options"][0]["_id"]}
    return {"surveyVersionId": version["_id"], "submissionCode": code or str(10**17 + secrets.randbelow(9 * 10**17)),
            "isAnonymous": anonymous, "answers": [{"questionId": key, "value": value} for key, value in values.items()]}


def test_registration_login_case_and_password(app):
    name = "User" + secrets.token_hex(3)
    first = Actor(app, name)
    second = Actor(app, name.lower())
    assert first.user["_id"] != second.user["_id"]
    duplicate = app.test_client().post("/api/auth/register", json={"username": name, "password": "password12"})
    assert duplicate.status_code == 409
    client = app.test_client()
    assert client.post("/api/auth/login", json={"username": name, "password": "Password12"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": name, "password": "password12"}).status_code == 200
    stored = app.extensions["db"].users.find_one({"username": name})
    assert stored["passwordHash"] != "password12"
    first.ok("POST", "/api/auth/logout", {})
    assert first.client.get("/api/auth/me").get_json()["user"] is None


@pytest.mark.parametrize("username,password", [("a b", "password12"), ("x" * 16, "password12"), ("test", "short"), ("test", "x" * 21), ("test", "pass word"), ("test", "password\n")])
def test_invalid_credentials(app, username, password):
    assert app.test_client().post("/api/auth/register", json={"username": username, "password": password}).status_code == 400


def test_session_csrf_origin_and_ownership(app):
    owner, stranger = Actor(app), Actor(app)
    assert app.test_client().get("/api/surveys").status_code == 401
    assert owner.client.post("/api/surveys", json={}).status_code == 403
    assert owner.client.post("/api/surveys", json={}, headers={"X-CSRF-Token": owner.csrf, "Origin": "https://evil.example"}).status_code == 403
    sid, version = published(owner)
    assert stranger.request("GET", f"/api/surveys/{sid}").status_code == 404
    assert stranger.request("GET", f"/api/surveys/{sid}/stats").status_code == 404
    assert stranger.request("GET", f"/api/surveys/{sid}/responses").status_code == 404
    assert stranger.ok("GET", "/api/surveys")["surveys"] == []
    assert stranger.ok("GET", f"/api/fill/{sid}")["version"]["_id"] == version["_id"]
    app.extensions["db"].sessions.update_one({"userId": ObjectId(stranger.user["_id"])}, {"$set": {"expiresAt": now() - timedelta(seconds=1)}})
    assert stranger.request("GET", f"/api/fill/{sid}").status_code == 401


def test_incomplete_draft_and_publish_validation(app):
    owner = Actor(app)
    survey = owner.ok("POST", "/api/surveys", {})
    sid = survey["_id"]
    assert owner.request("POST", f"/api/surveys/{sid}/publish", {"revision": 0}).status_code == 400
    survey.update(title="未完整", questions=[question()])
    survey["questions"][0]["options"] = []
    saved = owner.ok("PUT", f"/api/surveys/{sid}", survey)
    assert owner.request("POST", f"/api/surveys/{sid}/publish", {"revision": saved["revision"]}).status_code == 400
    assert app.extensions["db"].survey_versions.count_documents({"surveyId": ObjectId(sid)}) == 0


@pytest.mark.parametrize("mutate", [
    lambda q: q["options"][1].update(text=q["options"][0]["text"]),
    lambda q: q.update(options=q["options"] * 6),
    lambda q: q.update(validation={"maxSelections": 0}),
    lambda q: q.update(validation={"minSelections": 3}),
    lambda q: q.update(validation={"minSelections": 2, "maxSelections": 1}),
])
def test_invalid_choice_configurations(mutate):
    q = question("multiple")
    mutate(q)
    with pytest.raises(BusinessError):
        check_publish({"title": "标题", "description": "", "allowAnonymous": False, "allowMultipleSubmissions": False, "questions": [q]})


def test_impossible_integer_interval():
    q = question("number", integerOnly=True, minValue="0.2", maxValue="0.8")
    with pytest.raises(BusinessError, match="不存在整数"):
        check_publish({"title": "标题", "description": "", "allowAnonymous": False, "allowMultipleSubmissions": False, "questions": [q]})


@pytest.mark.parametrize("value", ["1e3", True, "NaN", "Infinity", "1" * 35])
def test_invalid_numeric_values(value):
    with pytest.raises(BusinessError):
        number(value)


def test_text_and_numeric_precision():
    text = question("text", minLength=2, maxLength=4)
    assert normalize_answer(text, "😀中") == "😀中"
    assert normalize_answer(text, " 中 ") == " 中 "
    with pytest.raises(BusinessError):
        normalize_answer(text, " \n")
    n = question("number", integerOnly=True, minValue="0", maxValue="120")
    assert normalize_answer(n, "0").to_decimal() == 0
    with pytest.raises(BusinessError):
        normalize_answer(n, "2.5")
    exact = "12345678901234567890.12345678901234"
    assert str(number(exact)) == exact


def test_nested_conditions_and_empty_answer():
    a, b, c = "a", "b", "c"
    condition = {"operator": "any", "conditions": [
        {"operator": "all", "conditions": [{"operator": "contains", "value": a}, {"operator": "contains", "value": b}]},
        {"operator": "contains", "value": c}]}
    assert evaluate(condition, [a, b])
    assert evaluate(condition, [c])
    assert not evaluate(condition, [a])
    negated = {"operator": "not", "conditions": [condition]}
    assert evaluate(negated, [a])
    assert evaluate(negated, None)
    assert not evaluate({"operator": "all", "conditions": [{"operator": "hasAnswer"}, negated]}, None)
    assert evaluate({"operator": "hasAnswer"}, Decimal128("0"))
    assert evaluate({"operator": "not", "conditions": [{"operator": "eq", "value": "18"}]}, Decimal128("19"))


def test_condition_depth_and_invalid_target():
    from engine import check_condition
    q = question("single")
    c = {"operator": "eq", "value": q["options"][0]["_id"]}
    for _ in range(4):
        c = {"operator": "not", "conditions": [c]}
    check_condition(c, q)
    with pytest.raises(BusinessError):
        check_condition({"operator": "not", "conditions": [c]}, q)
    with pytest.raises(BusinessError):
        check_condition({"operator": "all", "conditions": []}, q)
    with pytest.raises(BusinessError):
        check_condition({"operator": "not", "conditions": [c, c]}, q)
    q["jumpRules"] = [{"condition": {"operator": "hasAnswer"}, "targetQuestionId": q["_id"]}]
    with pytest.raises(BusinessError):
        check_publish({"title": "标题", "description": "", "allowAnonymous": False, "allowMultipleSubmissions": False, "questions": [q]})


def test_forward_jump_skip_required_and_prune_answers(app):
    owner, filler = Actor(app), Actor(app)
    q1, q2, q3 = question(), question("number", minValue="0"), question("text")
    q1["jumpRules"] = [{"condition": {"operator": "eq", "value": q1["options"][0]["_id"]}, "targetQuestionId": q3["_id"]}]
    sid, version = published(owner, [q1, q2, q3])
    values = {q1["_id"]: q1["options"][0]["_id"], q2["_id"]: "invalid hidden value", q3["_id"]: "有效文本"}
    filler.ok("POST", f"/api/fill/{sid}/submit", payload(version, values))
    stored = app.extensions["db"].responses.find_one({"surveyId": ObjectId(sid)})
    assert [a["questionId"] for a in stored["answers"]] == [q1["_id"], q3["_id"]]
    step = filler.ok("POST", f"/api/fill/{sid}/step", {"surveyVersionId": version["_id"], "questionId": q1["_id"], "value": q1["options"][0]["_id"]})
    assert step["nextQuestionId"] == q3["_id"]


def test_submission_required_invalid_options_and_duplicate_answers(app):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner)
    q = version["questions"][0]
    for answers in ([], [{"questionId": q["_id"], "value": str(ObjectId())}], [{"questionId": q["_id"], "value": None}]):
        data = payload(version);data["answers"] = answers
        assert filler.request("POST", f"/api/fill/{sid}/submit", data).status_code == 400
    data = payload(version);data["answers"] *= 2
    assert filler.request("POST", f"/api/fill/{sid}/submit", data).status_code == 400
    assert app.extensions["db"].responses.count_documents({"surveyId": ObjectId(sid)}) == 0


def test_idempotency_conflicts_and_anonymous_privacy(app):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner, anonymous=True)
    data = payload(version, anonymous=True)
    data.update(respondentId=owner.user["_id"], repeatRestricted=False)
    first = filler.ok("POST", f"/api/fill/{sid}/submit", data)
    replay = filler.ok("POST", f"/api/fill/{sid}/submit", data)
    assert first["responseId"] == replay["responseId"] and replay["replayed"]
    altered = copy.deepcopy(data)
    altered["answers"][0]["value"] = version["questions"][0]["options"][1]["_id"]
    assert filler.request("POST", f"/api/fill/{sid}/submit", altered).get_json()["code"] == "submission_conflict"
    doc = app.extensions["db"].responses.find_one({"_id": ObjectId(first["responseId"])})
    assert doc["respondentId"] == ObjectId(filler.user["_id"]) and doc["repeatRestricted"]
    public = owner.ok("GET", f"/api/surveys/{sid}/responses")["responses"][0]
    assert not {"respondentId", "username", "submissionCode", "payloadHash"} & set(public)
    survey = owner.ok("GET", f"/api/surveys/{sid}")
    owner.ok("POST", f"/api/surveys/{sid}/close", {"revision": survey["revision"]})
    assert filler.ok("POST", f"/api/fill/{sid}/submit", data)["replayed"]


def test_single_submission_concurrency(app):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner)
    barrier = Barrier(8)
    def send(_):
        actor = Actor(app, token=filler.token)
        barrier.wait()
        return actor.request("POST", f"/api/fill/{sid}/submit", payload(version)).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(8)))
    assert results.count(201) == 1 and results.count(409) == 7, results
    assert app.extensions["db"].responses.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_same_code_concurrent_retries(app):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner, multiple=True)
    data = payload(version)
    barrier = Barrier(6)
    def send(_):
        actor = Actor(app, token=filler.token)
        barrier.wait()
        return actor.request("POST", f"/api/fill/{sid}/submit", data)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(send, range(6)))
    assert all(r.status_code in (200, 201) for r in results)
    assert len({r.get_json()["responseId"] for r in results}) == 1
    assert app.extensions["db"].responses.count_documents({"surveyId": ObjectId(sid)}) == 1


def test_multiple_submissions_stats_and_decimal_average(app):
    owner, filler, other = Actor(app), Actor(app), Actor(app)
    q1, q2, q3, q4 = question(), question("multiple", minSelections=1, maxSelections=2), question("number"), question("text", required=False)
    sid, version = published(owner, [q1, q2, q3, q4], multiple=True)
    for actor, selection, numeric in [(filler, 0, "0.1"), (filler, 0, "0.2"), (filler, 1, "0.3"), (other, 0, "0.4")]:
        values = {q1["_id"]: q1["options"][selection]["_id"], q2["_id"]: [q2["options"][0]["_id"]], q3["_id"]: numeric}
        actor.ok("POST", f"/api/fill/{sid}/submit", payload(version, values))
    stats = owner.ok("GET", f"/api/surveys/{sid}/stats")
    assert stats["submissionCount"] == 4 and stats["respondentCount"] == 2
    assert [o["count"] for o in stats["questions"][0]["options"]] == [2, 1]
    assert [o["count"] for o in stats["questions"][1]["options"]] == [4, 0]
    assert stats["questions"][2]["average"] == "0.25"
    assert stats["questions"][3]["contents"] == []
    single = owner.ok("GET", f"/api/surveys/{sid}/stats/{q3['_id']}")
    assert len(single["questions"]) == 1


def test_versions_deadline_optimistic_edit_and_repeat_lock(app):
    owner, filler = Actor(app), Actor(app)
    sid, old_version = published(owner)
    filler.ok("POST", f"/api/fill/{sid}/submit", payload(old_version))
    survey = owner.ok("GET", f"/api/surveys/{sid}")
    assert owner.request("PUT", f"/api/surveys/{sid}", {"revision": survey["revision"], "title": "禁止直接修改"}).status_code == 409
    expired = owner.ok("PUT", f"/api/surveys/{sid}", {"revision": survey["revision"], "deadline": (now()-timedelta(seconds=1)).isoformat()})
    assert filler.request("GET", f"/api/fill/{sid}").status_code == 409
    resumed = owner.ok("PUT", f"/api/surveys/{sid}", {"revision": expired["revision"], "deadline": None})
    assert filler.request("GET", f"/api/fill/{sid}").status_code == 200
    assert owner.request("PUT", f"/api/surveys/{sid}", {"revision": expired["revision"], "deadline": None}).status_code == 409
    owner.ok("POST", f"/api/surveys/{sid}/close", {"revision": resumed["revision"]})
    assert filler.request("GET", f"/api/fill/{sid}").status_code == 409
    draft = owner.ok("GET", f"/api/surveys/{sid}")
    assert owner.request("PUT", f"/api/surveys/{sid}", {"revision": draft["revision"], "allowMultipleSubmissions": True}).status_code == 400
    draft["questions"][0]["options"][0]["text"] = "新版文字"
    draft = owner.ok("PUT", f"/api/surveys/{sid}", draft)
    new_version = owner.ok("POST", f"/api/surveys/{sid}/publish", {"revision": draft["revision"]})
    assert new_version["versionNumber"] == 2
    old_snapshot = app.extensions["db"].survey_versions.find_one({"_id": ObjectId(old_version["_id"])})
    assert old_snapshot["questions"][0]["options"][0]["text"] == "选项 A"
    newcomer = Actor(app)
    assert newcomer.request("POST", f"/api/fill/{sid}/submit", payload(old_version)).get_json()["code"] == "version_changed"
    assert filler.request("POST", f"/api/fill/{sid}/submit", payload(new_version)).get_json()["code"] == "already_submitted"
    assert owner.ok("GET", f"/api/surveys/{sid}/stats")["submissionCount"] == 0
    assert owner.ok("GET", f"/api/surveys/{sid}/stats?versionId={old_version['_id']}")["submissionCount"] == 1


def test_close_serializes_with_inflight_submission(app, monkeypatch):
    """Hold an insert inside the transaction: close must not overtake its survey write."""
    from pymongo.synchronous.collection import Collection
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner)
    survey = owner.ok("GET", f"/api/surveys/{sid}")
    reached, release = Event(), Event()
    original = Collection.insert_one
    def paused_insert(collection, document, *args, **kwargs):
        if collection.name == "responses" and collection.database.name == app.config["MONGO_DB"]:
            reached.set()
            assert release.wait(5), "测试未释放事务"
        return original(collection, document, *args, **kwargs)
    monkeypatch.setattr(Collection, "insert_one", paused_insert)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def submit_request():
            return Actor(app, token=filler.token).request("POST", f"/api/fill/{sid}/submit", payload(version))
        def close_request():
            return Actor(app, token=owner.token).request("POST", f"/api/surveys/{sid}/close", {"revision": survey["revision"]})
        submitting = pool.submit(submit_request)
        assert reached.wait(5)
        closing = pool.submit(close_request)
        try:
            with pytest.raises(TimeoutError):
                closing.result(timeout=.2)
        finally:
            release.set()
        assert submitting.result(timeout=10).status_code == 201
        assert closing.result(timeout=10).status_code == 200
    newcomer = Actor(app)
    assert newcomer.request("POST", f"/api/fill/{sid}/submit", payload(version)).get_json()["code"] == "closed"
