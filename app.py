import copy
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from bson import ObjectId, Decimal128, Int64
from flask import Flask, g, jsonify, render_template, request
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, DocumentTooLarge, PyMongoError
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from db import initialize_database
from template_library import register_library, protect_provenance
from engine import (BusinessError, require, check_shape, check_publish, normalize_answer,
                    normalize_path, next_question, canonical_number)


UTC = timezone.utc


def now():
    return datetime.now(UTC)


def oid(value):
    require(isinstance(value, str) and ObjectId.is_valid(value), "标识格式无效")
    return ObjectId(value)


def serialize(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Decimal128):
        return canonical_number(value)
    if isinstance(value, Int64):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialize(v) for v in value]
    return value


def deadline(value):
    if not value:
        return None
    require(isinstance(value, str), "截止时间格式无效")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(dt.tzinfo is not None, "截止时间需要包含时区")
        return dt.astimezone(UTC)
    except ValueError as exc:
        raise BusinessError("截止时间格式无效") from exc


def create_app(config=None):
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config.update(MONGO_URI=os.getenv("SURVEY_MONGO_URI", "mongodb://127.0.0.1:27019/?replicaSet=survey-stage2-rs"),
                      MONGO_DB=os.getenv("SURVEY_MONGO_DB", "survey_coursework_stage2"),
                      SESSION_HOURS=8, COOKIE_SECURE=False)
    if config:
        app.config.update(config)
    client = MongoClient(app.config["MONGO_URI"], tz_aware=True, serverSelectionTimeoutMS=5001)
    client.admin.command("ping")
    require(client.admin.command("hello").get("setName") is not None,
            "MongoDB 需运行副本集以支持事务，请使用 start.ps1 启动", status=503)
    db = client[app.config["MONGO_DB"]]
    initialize_database(db)
    app.extensions.update(mongo_client=client, db=db)

    def reply(data=None, status=200):
        return jsonify(serialize(data or {})), status

    def body():
        require(request.is_json, "请求需要使用 JSON")
        value = request.get_json()
        require(isinstance(value, dict), "请求内容必须是对象")
        return value

    @app.before_request
    def identify():
        g.user = g.login_session = None
        token = request.cookies.get("survey_session")
        if token:
            record = db.sessions.find_one({"_id": hashlib.sha256(token.encode()).hexdigest(), "expiresAt": {"$gt": now()}})
            if record:
                g.user = db.users.find_one({"_id": record["userId"]}, {"username": 1})
                g.login_session = record
        if request.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            require(not origin or origin == request.host_url.rstrip("/"), "请求来源不被允许", status=403)
            expected_user = request.headers.get("X-Survey-User")
            if expected_user is not None and request.path not in ("/api/auth/login", "/api/auth/register"):
                require(expected_user == (str(g.user["_id"]) if g.user else ""),
                        "其他页面的登录账号已变化，本次操作未执行，请确认当前账号", status=409, code="account_changed")
            if g.user and request.path not in ("/api/auth/login", "/api/auth/register"):
                require(secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), g.login_session["csrf"]), "会话校验失败，请刷新页面", status=403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Survey-User"] = str(g.user["_id"]) if g.user else ""
        return response

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            require(g.user is not None, "请先登录", status=401, code="login_required")
            return fn(*args, **kwargs)
        return wrapped

    def owned(sid, session=None):
        doc = db.surveys.find_one({"_id": oid(sid), "creatorId": g.user["_id"]}, session=session)
        require(doc is not None, "问卷不存在或无权访问", status=404)
        return doc

    def open_survey(sid, version_id, session=None):
        doc = db.surveys.find_one({"_id": oid(sid)}, session=session)
        require(doc is not None, "问卷不存在", status=404)
        require(doc["status"] == "published", "问卷尚未发布或已关闭", status=409, code="closed")
        require(doc.get("deadline") is None or doc["deadline"] > now(), "问卷已截止", status=409, code="expired")
        require(doc["publishedVersionId"] == oid(version_id), "问卷版本已更新，请保留旧草稿并重新填写", status=409, code="version_changed")
        return doc

    def transaction(callback):
        with client.start_session() as session:
            return session.with_transaction(callback)

    def credentials(data):
        username, password = data.get("username"), data.get("password")
        require(isinstance(username, str) and 1 <= len(username) <= 15 and bool(re.fullmatch(r"[A-Za-z0-9_\u3400-\u9fff]+", username)), "用户名限 1～15 个中文、英文字母、数字或下划线，不允许空格")
        require(isinstance(password, str) and 8 <= len(password) <= 20 and not re.search(r"\s", password), "密码限 8～20 个字符，不允许空白")
        return username, password

    def begin_session(user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expires = now() + timedelta(hours=app.config["SESSION_HOURS"])
        db.sessions.insert_one({"_id": hashlib.sha256(token.encode()).hexdigest(), "userId": user["_id"], "csrf": csrf, "expiresAt": expires})
        if g.login_session:
            db.sessions.delete_one({"_id": g.login_session["_id"]})
        response = jsonify(user={"_id": str(user["_id"]), "username": user["username"]}, csrf=csrf)
        response.set_cookie("survey_session", token, httponly=True, samesite="Lax", secure=app.config["COOKIE_SECURE"], expires=expires)
        return response

    @app.get("/")
    @app.get("/library")
    @app.get("/survey/<sid>")
    def page(sid=None):
        return render_template("index.html")

    @app.get("/api/auth/me")
    def me():
        return reply({"user": g.user, "csrf": g.login_session["csrf"] if g.login_session else None})

    @app.post("/api/auth/register")
    def register():
        username, password = credentials(body())
        user = {"_id": ObjectId(), "username": username, "passwordHash": generate_password_hash(password), "createdAt": now()}
        try:
            db.users.insert_one(user)
        except DuplicateKeyError as exc:
            raise BusinessError("用户名已存在", 409, "username_taken") from exc
        return begin_session(user)

    @app.post("/api/auth/login")
    def login():
        username, password = credentials(body())
        user = db.users.find_one({"username": username})
        require(user is not None and check_password_hash(user["passwordHash"], password), "用户名或密码错误", status=401)
        return begin_session(user)

    @app.post("/api/auth/logout")
    @login_required
    def logout():
        db.sessions.delete_one({"_id": g.login_session["_id"]})
        response = jsonify({})
        response.delete_cookie("survey_session")
        return response

    @app.get("/api/surveys")
    @login_required
    def list_surveys():
        return reply({"surveys": list(db.surveys.find({"creatorId": g.user["_id"]}, {"questions": 0}).sort("updatedAt", -1))})

    @app.post("/api/surveys")
    @login_required
    def create_survey():
        stamp = now()
        doc = {"_id": ObjectId(), "creatorId": g.user["_id"], "title": "", "description": "", "status": "draft",
               "allowAnonymous": False, "allowMultipleSubmissions": False, "deadline": None, "questions": [],
               "publishedVersionId": None, "versionCounter": 0, "revision": 0, "submissionFence": 0,
               "createdAt": stamp, "updatedAt": stamp}
        db.surveys.insert_one(doc)
        return reply(doc, 201)

    @app.get("/api/surveys/<sid>")
    @login_required
    def get_survey(sid):
        return reply(owned(sid))

    @app.put("/api/surveys/<sid>")
    @login_required
    def update_survey(sid):
        data = body()
        original = owned(sid)
        revision = data.get("revision")
        require(type(revision) is int and revision == original["revision"], "编辑内容已被其他页面更新，请重新载入", status=409, code="edit_conflict")
        editable = {"title", "description", "allowAnonymous", "allowMultipleSubmissions", "questions", "deadline"}
        changes = {key: data[key] for key in editable if key in data}
        if "deadline" in changes:
            changes["deadline"] = deadline(changes["deadline"])
        candidate = {**original, **changes}
        check_shape(candidate)
        protect_provenance(original, candidate)
        require(not original["versionCounter"] or candidate["allowMultipleSubmissions"] == original["allowMultipleSubmissions"], "重复填写设置首次发布后不可修改")
        if original["status"] == "published":
            require(all(candidate[key] == original[key] for key in editable - {"deadline"}), "请先关闭问卷，再修改内容", status=409)
        updated = db.surveys.find_one_and_update({"_id": original["_id"], "revision": revision},
                    {"$set": {**changes, "updatedAt": now()}, "$inc": {"revision": 1}}, return_document=ReturnDocument.AFTER)
        require(updated is not None, "编辑冲突，请重新载入", status=409, code="edit_conflict")
        return reply(updated)

    @app.post("/api/surveys/<sid>/publish")
    @login_required
    def publish(sid):
        expected = body().get("revision")
        def commit(session):
            survey = owned(sid, session)
            require(type(expected) is int and survey["revision"] == expected, "编辑内容已变，请重新载入", status=409)
            require(survey["status"] == "draft", "问卷已经发布", status=409)
            check_publish(survey)
            version = {k: copy.deepcopy(survey[k]) for k in ("title", "description", "allowAnonymous", "questions")}
            version.update(_id=ObjectId(), surveyId=survey["_id"], versionNumber=survey["versionCounter"] + 1, publishedAt=now())
            db.survey_versions.insert_one(version, session=session)
            db.surveys.update_one({"_id": survey["_id"]}, {"$set": {"status": "published", "publishedVersionId": version["_id"], "versionCounter": version["versionNumber"], "updatedAt": now()}, "$inc": {"revision": 1}}, session=session)
            return version
        return reply(transaction(commit), 201)

    @app.post("/api/surveys/<sid>/close")
    @login_required
    def close_survey(sid):
        expected = body().get("revision")
        def commit(session):
            survey = owned(sid, session)
            require(type(expected) is int and expected == survey["revision"], "编辑内容已变，请重新载入", status=409)
            require(survey["status"] == "published", "问卷已经处于草稿", status=409)
            db.surveys.update_one({"_id": survey["_id"]}, {"$set": {"status": "draft", "updatedAt": now()}, "$inc": {"revision": 1}}, session=session)
            return {"status": "draft"}
        return reply(transaction(commit))

    @app.get("/api/fill/<sid>")
    @login_required
    def filling(sid):
        survey = db.surveys.find_one({"_id": oid(sid)})
        require(survey is not None, "问卷不存在", status=404)
        open_survey(sid, str(survey.get("publishedVersionId")))
        version = db.survey_versions.find_one({"_id": survey["publishedVersionId"]})
        return reply({"surveyId": survey["_id"], "version": version, "deadline": survey["deadline"], "allowMultipleSubmissions": survey["allowMultipleSubmissions"]})

    @app.post("/api/fill/<sid>/step")
    @login_required
    def step(sid):
        data = body()
        survey = open_survey(sid, data.get("surveyVersionId"))
        version = db.survey_versions.find_one({"_id": survey["publishedVersionId"]})
        index = next((i for i, q in enumerate(version["questions"]) if q["_id"] == data.get("questionId")), None)
        require(index is not None, "题目不存在")
        value = normalize_answer(version["questions"][index], data.get("value"))
        return reply({"nextQuestionId": next_question(version["questions"], index, value)})

    def prepare_submission(sid, data):
        require(isinstance(data.get("submissionCode"), str) and bool(re.fullmatch(r"[1-9][0-9]{17}", data["submissionCode"])), "提交校验码必须是 18 位数字字符串")
        require(type(data.get("isAnonymous")) is bool, "匿名设置格式无效")
        version = db.survey_versions.find_one({"_id": oid(data.get("surveyVersionId")), "surveyId": oid(sid)})
        require(version is not None, "发布版本不存在", status=409)
        require(not data["isAnonymous"] or version["allowAnonymous"], "此问卷不允许匿名")
        answers = normalize_path(version, data.get("answers"))
        digest_data = serialize({"surveyId": oid(sid), "surveyVersionId": version["_id"], "respondentId": g.user["_id"], "isAnonymous": data["isAnonymous"], "answers": answers})
        digest = hashlib.sha256(json.dumps(digest_data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return version, answers, digest, Int64(int(data["submissionCode"]))

    def repeat_result(code, digest):
        existing = db.responses.find_one({"submissionCode": code})
        if not existing:
            return None
        require(existing["respondentId"] == g.user["_id"] and existing["payloadHash"] == digest,
                "校验码已经用于不同内容，请勿覆盖原答卷", status=409, code="submission_conflict")
        return {"responseId": existing["_id"], "submittedAt": existing["submittedAt"], "replayed": True}

    @app.post("/api/fill/<sid>/submit")
    @login_required
    def submit(sid):
        data = body()
        version, answers, digest, code = prepare_submission(sid, data)
        previous = repeat_result(code, digest)
        if previous:
            return reply(previous)
        def commit(session):
            survey = open_survey(sid, str(version["_id"]), session)
            # Writing the survey serializes a submission with close/publish/deadline changes.
            db.surveys.update_one({"_id": survey["_id"]}, {"$inc": {"submissionFence": 1}}, session=session)
            response = {"_id": ObjectId(), "surveyId": survey["_id"], "surveyVersionId": version["_id"],
                        "respondentId": g.user["_id"], "submissionCode": code, "repeatRestricted": not survey["allowMultipleSubmissions"],
                        "isAnonymous": data["isAnonymous"], "answers": answers, "submittedAt": now(), "payloadHash": digest}
            db.responses.insert_one(response, session=session)
            return {"responseId": response["_id"], "submittedAt": response["submittedAt"], "replayed": False}
        try:
            return reply(transaction(commit), 201)
        except DuplicateKeyError as exc:
            previous = repeat_result(code, digest)
            if previous:
                return reply(previous)
            raise BusinessError("你已经提交过该问卷，不能重复填写", 409, "already_submitted") from exc

    @app.get("/api/surveys/<sid>/versions")
    @login_required
    def versions(sid):
        owned(sid)
        return reply({"versions": list(db.survey_versions.find({"surveyId": oid(sid)}, {"questions": 0}).sort("versionNumber", -1))})

    def stats_version(sid):
        survey = owned(sid)
        version_id = request.args.get("versionId") or survey.get("publishedVersionId")
        require(version_id is not None, "尚无发布版本", status=404)
        version_id = str(version_id)
        version = db.survey_versions.find_one({"_id": oid(version_id), "surveyId": survey["_id"]})
        require(version is not None, "尚无发布版本", status=404)
        return survey, version

    @app.get("/api/surveys/<sid>/stats")
    @app.get("/api/surveys/<sid>/stats/<qid>")
    @login_required
    def stats(sid, qid=None):
        survey, version = stats_version(sid)
        match = {"surveyId": survey["_id"], "surveyVersionId": version["_id"]}
        questions = version["questions"]
        if qid:
            questions = [q for q in questions if q["_id"] == qid]
            require(bool(questions), "题目不存在", status=404)
        totals = list(db.responses.aggregate([{"$match": match}, {"$group": {"_id": "$respondentId", "n": {"$sum": 1}}}, {"$group": {"_id": None, "respondentCount": {"$sum": 1}, "submissionCount": {"$sum": "$n"}}}]))
        output = []
        for q in questions:
            base = [{"$match": match}, {"$unwind": "$answers"}, {"$match": {"answers.questionId": q["_id"]}}]
            people = list(db.responses.aggregate(base + [{"$group": {"_id": "$respondentId"}}, {"$count": "n"}]))
            item = {"questionId": q["_id"], "title": q["title"], "type": q["type"], "respondentCount": people[0]["n"] if people else 0}
            if q["type"] in ("single", "multiple"):
                pipeline = base + ([{"$unwind": "$answers.value"}] if q["type"] == "multiple" else [])
                if q["type"] == "single":
                    pipeline += [{"$group": {"_id": {"option": "$answers.value", "user": "$respondentId"}}}, {"$group": {"_id": "$_id.option", "n": {"$sum": 1}}}]
                else:
                    pipeline += [{"$group": {"_id": "$answers.value", "n": {"$sum": 1}}}]
                counts = {r["_id"]: r["n"] for r in db.responses.aggregate(pipeline)}
                item["options"] = [{"optionId": o["_id"], "text": o["text"], "count": counts.get(o["_id"], 0)} for o in q["options"]]
            else:
                rows = list(db.responses.aggregate(base + [{"$project": {"_id": 0, "value": "$answers.value", "submittedAt": 1}}]))
                item["contents"] = rows
                if q["type"] == "number":
                    averages = list(db.responses.aggregate(base + [{"$group": {"_id": None, "average": {"$avg": "$answers.value"}}}]))
                    item["average"] = averages[0]["average"] if averages else None
            output.append(item)
        return reply({"versionId": version["_id"], "versionNumber": version["versionNumber"],
                      "submissionCount": totals[0]["submissionCount"] if totals else 0,
                      "respondentCount": totals[0]["respondentCount"] if totals else 0, "questions": output})

    @app.get("/api/surveys/<sid>/responses")
    @login_required
    def response_list(sid):
        survey, version = stats_version(sid)
        results = []
        for doc in db.responses.find({"surveyId": survey["_id"], "surveyVersionId": version["_id"]}).sort("submittedAt", -1):
            item = {k: doc[k] for k in ("_id", "surveyVersionId", "answers", "submittedAt", "isAnonymous")}
            if not doc["isAnonymous"]:
                user = db.users.find_one({"_id": doc["respondentId"]})
                item["username"] = user["username"] if user else "用户不存在"
            results.append(item)
        return reply({"responses": results})

    @app.errorhandler(BusinessError)
    def business_error(exc):
        return reply({"error": exc.message, "code": exc.code, **exc.details}, exc.status)

    @app.errorhandler(DocumentTooLarge)
    def too_large(exc):
        return reply({"error": "内容超过 MongoDB 单文档容量，未保存；请减少内容后重试", "code": "document_too_large"}, 413)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return reply({"error": exc.description, "code": "http_error"}, exc.code)

    @app.errorhandler(PyMongoError)
    def database_error(exc):
        app.logger.exception("MongoDB operation failed")
        return reply({"error": "数据库操作失败，本次操作未确认成功，请使用原请求重试", "code": "database_error"}, 503)

    register_library(app, db, client, login_required, owned, reply, body, transaction, oid, now)
    return app


if __name__ == "__main__":
    from waitress import serve
    serve(create_app(), host="127.0.0.1", port=int(os.getenv("SURVEY_PORT", "5001")), threads=8)

