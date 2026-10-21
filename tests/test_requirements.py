"""Requirement gaps: route-to-storage integration, answer boundaries, distinct users."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from bson import ObjectId

from test_system import Actor, payload, published, question


# Share the real-database session fixture defined alongside the original tests.
from test_system import app  # noqa: F401


def assert_complete_route(owner, filler, sid, version, first_value, matched):
    """Check step routing AND independently recomputed submitted answers in MongoDB."""
    first, middle, last = version["questions"]
    exposed = filler.ok("GET", f"/api/fill/{sid}")
    assert exposed["version"]["_id"] == version["_id"]
    step = filler.ok("POST", f"/api/fill/{sid}/step", {
        "surveyVersionId": version["_id"], "questionId": first["_id"], "value": first_value})
    expected_next = last["_id"] if matched else middle["_id"]
    assert step["nextQuestionId"] == expected_next
    # Hidden invalid content must be pruned, while the fallback required question
    # must be answered when the rule does not match.
    values = {first["_id"]: first_value,
              middle["_id"]: {"hidden": "must not be validated or saved"} if matched else "默认路径答案",
              last["_id"]: "结束题答案"}
    if not matched:
        next_step = filler.ok("POST", f"/api/fill/{sid}/step", {
            "surveyVersionId": version["_id"], "questionId": middle["_id"], "value": values[middle["_id"]]})
        assert next_step["nextQuestionId"] == last["_id"]
    final_step = filler.ok("POST", f"/api/fill/{sid}/step", {
        "surveyVersionId": version["_id"], "questionId": last["_id"], "value": values[last["_id"]]})
    assert final_step["nextQuestionId"] is None
    saved = filler.ok("POST", f"/api/fill/{sid}/submit", payload(version, values))
    doc = owner.app.extensions["db"].responses.find_one({"_id": ObjectId(saved["responseId"])})
    expected_path = [first["_id"], last["_id"]] if matched else [first["_id"], middle["_id"], last["_id"]]
    assert [answer["questionId"] for answer in doc["answers"]] == expected_path
    assert doc["surveyId"] == ObjectId(sid) and doc["surveyVersionId"] == ObjectId(version["_id"])
    assert doc["respondentId"] == ObjectId(filler.user["_id"])
    stats = owner.ok("GET", f"/api/surveys/{sid}/stats")
    assert stats["submissionCount"] == stats["respondentCount"] == 1
    assert stats["questions"][1]["respondentCount"] == (0 if matched else 1)
    assert stats["questions"][2]["contents"][0]["value"] == "结束题答案"


@pytest.mark.parametrize("formula,selection,matched", [
    ("a_without_c", [0], True),
    ("a_without_c", [0, 2], False),
    ("a_without_c", [1], False),
    ("a_and_b_or_c", [0, 1], True),
    ("a_and_b_or_c", [2], True),
    ("a_and_b_or_c", [0], False),
    ("a_and_b_or_c", [1], False),
    ("a_and_b_or_c", [0, 1, 2], True),
], ids=["A-without-C", "AC-rejected", "B-rejected", "AB-matched", "C-matched", "A-fallback", "B-fallback", "ABC-matched"])
def test_multiple_jump_full_submission(app, formula, selection, matched):
    owner, filler = Actor(app), Actor(app)
    first, middle, last = question("multiple"), question("text"), question("text")
    first["options"].append({"_id": str(ObjectId()), "text": "选项 C"})
    a, b, c = [option["_id"] for option in first["options"]]
    if formula == "a_without_c":
        condition = {"operator": "all", "conditions": [
            {"operator": "contains", "value": a},
            {"operator": "not", "conditions": [{"operator": "contains", "value": c}]}]}
    else:
        condition = {"operator": "any", "conditions": [
            {"operator": "all", "conditions": [{"operator": "contains", "value": a}, {"operator": "contains", "value": b}]},
            {"operator": "contains", "value": c}]}
    first["jumpRules"] = [{"condition": {"operator": "all", "conditions": [{"operator": "hasAnswer"}, condition]}, "targetQuestionId": last["_id"]}]
    sid, version = published(owner, [first, middle, last])
    value = [first["options"][index]["_id"] for index in selection]
    assert_complete_route(owner, filler, sid, version, value, matched)


@pytest.mark.parametrize("operator,comparison,value,matched", [
    ("eq", "18", "18", True), ("eq", "18", "19", False),
    ("gt", "18", "18", False), ("gt", "18", "18.01", True),
    ("gte", "18", "18", True), ("gte", "18", "17.99", False),
    ("lt", "18", "18", False), ("lt", "18", "17.99", True),
    ("lte", "18", "18", True), ("lte", "18", "18.01", False),
    ("between", {"min": "18", "max": "30"}, "18", True),
    ("between", {"min": "18", "max": "30"}, "30", True),
    ("between", {"min": "18", "max": "30"}, "17.99", False),
    ("between", {"min": "18", "max": "30"}, "30.01", False),
    ("not_eq", "18", "19", True), ("not_eq", "18", "18", False),
    ("gte", "0", "0", True),
], ids=["eq-hit", "eq-fallback", "gt-edge", "gt-hit", "gte-edge", "gte-fallback",
        "lt-edge", "lt-hit", "lte-edge", "lte-fallback", "range-min", "range-max",
        "range-below", "range-above", "not-eq-hit", "not-eq-fallback", "zero-is-answer"])
def test_numeric_jump_full_submission(app, operator, comparison, value, matched):
    owner, filler = Actor(app), Actor(app)
    first, middle, last = question("number"), question("text"), question("text")
    condition = {"operator": "eq" if operator == "not_eq" else operator, "value": comparison}
    if operator == "not_eq":
        condition = {"operator": "not", "conditions": [condition]}
    first["jumpRules"] = [{"condition": {"operator": "all", "conditions": [{"operator": "hasAnswer"}, condition]}, "targetQuestionId": last["_id"]}]
    sid, version = published(owner, [first, middle, last])
    assert_complete_route(owner, filler, sid, version, value, matched)
    stored = app.extensions["db"].responses.find_one({"surveyId": ObjectId(sid)})
    assert stored["answers"][0]["value"].to_decimal() == Decimal(value)


def assert_boundary_submission(owner, filler, sid, version, value, accepted, message):
    first = version["questions"][0]
    step = filler.request("POST", f"/api/fill/{sid}/step", {
        "surveyVersionId": version["_id"], "questionId": first["_id"], "value": value})
    result = filler.request("POST", f"/api/fill/{sid}/submit", payload(version, {first["_id"]: value}))
    match = {"surveyId": ObjectId(sid)}
    if accepted:
        assert step.status_code == 200, step.get_json()
        assert step.get_json()["nextQuestionId"] is None
        assert result.status_code == 201, result.get_json()
        stored = owner.app.extensions["db"].responses.find_one(match)
        assert stored is not None and len(stored["answers"]) == 1
        actual = stored["answers"][0]["value"]
        if first["type"] == "multiple":
            assert actual == sorted(value)
        elif first["type"] == "number":
            assert actual.to_decimal() == Decimal(value)
        else:
            assert actual == value
        assert owner.ok("GET", f"/api/surveys/{sid}/stats")["submissionCount"] == 1
    else:
        assert step.status_code == result.status_code == 400
        assert message in step.get_json()["error"]
        assert message in result.get_json()["error"]
        assert owner.app.extensions["db"].responses.count_documents(match) == 0
        assert owner.ok("GET", f"/api/surveys/{sid}/stats")["submissionCount"] == 0


@pytest.mark.parametrize("lo,hi,selection,accepted,message", [
    (2, 3, [0], False, "少于"),
    (2, 3, [0, 1], True, ""),
    (2, 3, [0, 1, 2], True, ""),
    (2, 3, [0, 1, 2, 3], False, "超过"),
    (2, 2, [0], False, "少于"),
    (2, 2, [0, 1], True, ""),
    (2, 2, [0, 1, 2], False, "超过"),
    (2, 3, [], False, "必须回答"),
    (2, 3, [0, 0], False, "重复"),
    (2, 3, [0, "foreign"], False, "不属于"),
], ids=["below-min", "at-min", "at-max", "above-max", "exact-too-few", "exact-valid",
        "exact-too-many", "required-empty", "duplicate-option", "foreign-option"])
def test_multiple_answer_boundaries_full_submit(app, lo, hi, selection, accepted, message):
    owner, filler = Actor(app), Actor(app)
    q = question("multiple", minSelections=lo, maxSelections=hi)
    q["options"].extend([{"_id": str(ObjectId()), "text": "选项 C"}, {"_id": str(ObjectId()), "text": "选项 D"}])
    sid, version = published(owner, [q])
    value = [str(ObjectId()) if index == "foreign" else q["options"][index]["_id"] for index in selection]
    assert_boundary_submission(owner, filler, sid, version, value, accepted, message)


@pytest.mark.parametrize("value,accepted,message", [
    ("中", False, "少于"), ("中文", True, ""), ("中文数", True, ""),
    ("中文数字", True, ""), ("中文数字啊", False, "超过"),
    ("😀中", True, ""), (" 中 ", True, ""), ("中\n文", True, ""),
], ids=["below-min", "at-min", "within-range", "at-max", "above-max", "unicode-codepoints", "preserve-spaces", "preserve-newline"])
def test_text_answer_boundaries_full_submit(app, value, accepted, message):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner, [question("text", minLength=2, maxLength=4)])
    assert_boundary_submission(owner, filler, sid, version, value, accepted, message)


@pytest.mark.parametrize("validation,value,accepted,message", [
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "-1", False, "小于"),
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "0", True, ""),
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "1", True, ""),
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "120", True, ""),
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "121", False, "大于"),
    ({"minValue": "0", "maxValue": "120", "integerOnly": True}, "2.5", False, "整数"),
    ({"minValue": "-1", "maxValue": "1"}, "-1", True, ""),
    ({"minValue": "-1", "maxValue": "1"}, "1", True, ""),
    ({"minValue": "-1", "maxValue": "1"}, "-1.01", False, "小于"),
    ({"minValue": "-1", "maxValue": "1"}, "1.01", False, "大于"),
    ({"minValue": "-1", "maxValue": "1"}, "0.25", True, ""),
    ({"integerOnly": True}, "13800138000", True, ""),
], ids=["age-below", "age-zero", "age-valid", "age-max", "age-above", "age-fraction",
        "decimal-min", "decimal-max", "decimal-below", "decimal-above", "decimal-valid", "phone-integer"])
def test_numeric_answer_boundaries_full_submit(app, validation, value, accepted, message):
    owner, filler = Actor(app), Actor(app)
    sid, version = published(owner, [question("number", **validation)])
    assert_boundary_submission(owner, filler, sid, version, value, accepted, message)


@pytest.mark.parametrize("multiple", [False, True], ids=["repeat-forbidden", "repeat-allowed"])
def test_distinct_users_concurrent_submission_and_statistics(app, multiple):
    owner = Actor(app)
    actors = [Actor(app) for _ in range(8)]
    sid, version = published(owner, multiple=multiple)
    q = version["questions"][0]
    data = [payload(version, {q["_id"]: q["options"][index % 2]["_id"]}) for index in range(8)]
    barrier = Barrier(8)
    def send(index):
        actor = Actor(app, token=actors[index].token)
        barrier.wait(timeout=10)
        return actor.request("POST", f"/api/fill/{sid}/submit", data[index])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(8)))
    assert all(result.status_code == 201 for result in results), [(r.status_code, r.get_json()) for r in results]
    documents = list(app.extensions["db"].responses.find({"surveyId": ObjectId(sid)}))
    assert len(documents) == 8
    assert {doc["respondentId"] for doc in documents} == {ObjectId(actor.user["_id"]) for actor in actors}
    assert len({doc["submissionCode"] for doc in documents}) == len({doc["_id"] for doc in documents}) == 8
    stats = owner.ok("GET", f"/api/surveys/{sid}/stats")
    assert stats["submissionCount"] == stats["respondentCount"] == 8
    assert [o["count"] for o in stats["questions"][0]["options"]] == [4, 4]
    for index, actor in enumerate(actors):
        newer = payload(version, {q["_id"]: q["options"][index % 2]["_id"]})
        result = actor.request("POST", f"/api/fill/{sid}/submit", newer)
        assert result.status_code == (201 if multiple else 409), result.get_json()
        if not multiple:
            assert result.get_json()["code"] == "already_submitted"
    # A retry of each original request must still refer to its original response.
    for index, actor in enumerate(actors):
        retried = actor.ok("POST", f"/api/fill/{sid}/submit", data[index])
        assert retried["replayed"] and retried["responseId"] == results[index].get_json()["responseId"]
    stats = owner.ok("GET", f"/api/surveys/{sid}/stats")
    expected = 16 if multiple else 8
    assert stats["submissionCount"] == expected and stats["respondentCount"] == 8
    assert [o["count"] for o in stats["questions"][0]["options"]] == [4, 4]
    assert app.extensions["db"].responses.count_documents({"surveyId": ObjectId(sid)}) == expected
