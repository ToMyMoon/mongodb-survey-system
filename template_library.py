"""Stage two catalogue, immutable snapshots, provenance and owner-scoped statistics."""
import copy
import hashlib
import json
from collections import Counter
from decimal import Decimal, localcontext

from bson import ObjectId, Decimal128
from flask import g, request

from engine import BusinessError, require, check_publish, number, canonical_number, valid_id


CONTENT = ("type", "title", "options", "required", "validation")
PROVENANCE = ("sourceTemplateId", "savedTemplateIds")


def content(question):
    return {key: copy.deepcopy(question[key]) for key in CONTENT}


def signature(question, statistics=False):
    value = content(question)
    value["options"] = [option["text"] for option in value["options"]]
    if statistics:
        value.pop("required")
    for key in ("minValue", "maxValue"):
        if key in value["validation"]:
            try:
                value["validation"][key] = canonical_number(Decimal128(number(value["validation"][key])))
            except BusinessError:
                # Incomplete draft configurations are allowed; they simply cannot
                # match a validated template or enter template statistics.
                value["validation"][key] = {"unfinished": value["validation"][key]}
    if value["type"] == "number":
        value["validation"].setdefault("integerOnly", False)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_template(question):
    q = {**content(question), "_id": question["_id"], "jumpRules": []}
    check_publish({"title": "模板", "description": "", "allowAnonymous": False,
                   "allowMultipleSubmissions": False, "questions": [q]})
    return content(q)


def protect_provenance(original, candidate):
    previous = {q["_id"]: q for q in original["questions"]}
    for q in candidate["questions"]:
        before = previous.get(q["_id"], {})
        if "savedTemplateIds" in q:
            ids = q["savedTemplateIds"]
            require(isinstance(ids, list) and all(valid_id(tid) for tid in ids)
                    and len(set(ids)) == len(ids), "已保存模板来源必须是有效且不重复的列表")
        require(q.get("sourceTemplateId") is None or valid_id(q["sourceTemplateId"]), "模板来源标识无效")
        for key in PROVENANCE:
            require(q.get(key) == before.get(key), "模板来源由系统维护，请通过选用模板添加题目")


def exact_average(values):
    if not values:
        return None
    # Decimal128 values may span thousands of places. Sum exactly before rounding
    # the division, so 10^100 + 1 - 10^100 does not lose the middle value.
    with localcontext() as ctx:
        ctx.prec = max(50, max(v.adjusted() for v in values)
                       - min(v.as_tuple().exponent for v in values) + len(str(len(values))) + 2)
        total = sum(values, Decimal(0))
        ctx.prec = 50
        return format(total / len(values), "f")


def register_library(app, db, client, login_required, owned, reply, body, transaction, oid, now):
    # A small coursework catalogue: serialize catalogue writes in a transaction.
    # This makes revoke/use, bank content uniqueness and restore atomic together.
    db.template_state.update_one({"_id": "catalogue"}, {"$setOnInsert": {"revision": 0}}, upsert=True)

    def lock(session):
        db.template_state.update_one({"_id": "catalogue"}, {"$inc": {"revision": 1}}, session=session)

    def write(fn):
        def run(session):
            lock(session)
            return fn(session)
        return transaction(run)

    def migrate(session):
        state = db.template_state.find_one({"_id": "catalogue"}, session=session)
        if state.get("personalEntriesVersion") == 1:
            return
        for doc in db.question_templates.find({}, session=session):
            db.template_entries.update_one({"userId": doc["ownerId"], "templateId": doc["_id"]},
                {"$setOnInsert": {"removed": doc["deleted"], "createdAt": doc["createdAt"]}}, upsert=True, session=session)
        for share in db.question_shares.find({"active": True}, session=session):
            db.template_entries.update_one({"userId": share["userId"], "templateId": share["templateId"]},
                {"$setOnInsert": {"removed": False, "createdAt": now()}}, upsert=True, session=session)
        # Legacy deletion is now represented by the author's personal entry.
        # Previously revoked grants stay revoked; migration never regrants access.
        db.question_templates.update_many({"deleted": True}, {"$set": {"deleted": False}}, session=session)
        db.template_state.update_one({"_id": "catalogue"}, {"$set": {"personalEntriesVersion": 1}}, session=session)

    write(migrate)

    def entry(doc, session=None, uid=None):
        return db.template_entries.find_one({"templateId": doc["_id"], "userId": uid or g.user["_id"]}, session=session)

    def accessible(doc, session=None):
        return doc["ownerId"] == g.user["_id"] or bool(db.question_shares.find_one(
            {"templateId": doc["_id"], "userId": g.user["_id"], "active": True}, session=session))

    def collect(doc, session, uid=None):
        db.template_entries.update_one({"templateId": doc["_id"], "userId": uid or g.user["_id"]},
            {"$setOnInsert": {"removed": False, "createdAt": now(), "classificationChosen": True}}, upsert=True, session=session)

    def template(tid, session=None, owner=False, history=False):
        doc = db.question_templates.find_one({"_id": oid(tid)}, session=session)
        require(doc is not None, "模板不存在或无权访问", status=404)
        mine = doc["ownerId"] == g.user["_id"]
        if owner:
            require(mine, "只能管理自己的模板", status=404)
        require(accessible(doc, session), "当前没有此模板的共享权限，来源记录仍保留", status=404)
        personal = entry(doc, session)
        if not history:
            require(not personal or not personal["removed"], "已从我的模板移除，请先恢复", status=409)
        return doc

    def bank(bid, session=None):
        doc = db.question_banks.find_one({"_id": oid(bid), "ownerId": g.user["_id"], "deleted": False}, session=session)
        require(doc is not None, "题库不存在或无权访问", status=404)
        return doc

    def add_item(bid, doc, session):
        b = bank(bid, session)
        existing = db.question_bank_items.find_one({"bankId": b["_id"], "templateId": doc["_id"]}, session=session)
        if existing and existing["active"]:
            return
        duplicate = db.question_bank_items.find_one({"bankId": b["_id"], "fingerprint": doc["fingerprint"], "active": True}, session=session)
        require(duplicate is None, "该题库已有内容相同的模板", status=409, code="duplicate_content")
        db.question_bank_items.update_one({"bankId": b["_id"], "templateId": doc["_id"]},
            {"$set": {"active": True, "fingerprint": doc["fingerprint"], "updatedAt": now()},
             "$setOnInsert": {"createdAt": now(), "position": db.question_bank_items.count_documents({"bankId": b["_id"]}, session=session)}}, upsert=True, session=session)

    def default_bank(session, uid=None):
        uid = uid or g.user["_id"]
        if not db.question_banks.find_one({"ownerId": uid, "isDefault": True}, session=session):
            legacy = db.question_banks.find_one({"ownerId": uid, "name": "默认题库", "deleted": False}, session=session)
            if legacy:
                db.question_banks.update_one({"_id": legacy["_id"]}, {"$set": {"isDefault": True}}, session=session)
        db.question_banks.update_one({"ownerId": uid, "isDefault": True}, {"$setOnInsert": {
            "_id": ObjectId(), "name": "默认题库", "description": "保存模板时默认选中，可另选其他题库。",
            "deleted": False, "createdAt": now()}}, upsert=True, session=session)
        return db.question_banks.find_one({"ownerId": uid, "isDefault": True}, session=session)

    def make_bank(data, session):
        name, description = data.get("name"), data.get("description", "")
        require(isinstance(name, str) and bool(name.strip()) and len(name.strip()) <= 100
                and isinstance(description, str), "请填写 1～100 字的题库名称和有效说明")
        name = name.strip()
        require(name not in ("默认题库", "全部题库"), "该名称用于系统题库，请换一个名称", code="reserved_bank_name")
        require(not db.question_banks.find_one({"ownerId": g.user["_id"], "name": name, "deleted": False}, session=session),
                "已有同名题库", status=409)
        doc = {"_id": ObjectId(), "ownerId": g.user["_id"], "name": name, "description": description, "deleted": False, "createdAt": now()}
        db.question_banks.insert_one(doc, session=session)
        return doc

    def classify(doc, data, session, use_default=False):
        ids, names = data.get("bankIds"), data.get("newBanks", [])
        require(isinstance(names, list) and len(names) <= 30 and all(isinstance(n, str) for n in names), "新题库名称列表无效")
        if ids is None:
            ids = [data["bankId"]] if data.get("bankId") else ([str(default_bank(session)["_id"])] if use_default else [])
        require(isinstance(ids, list) and len(ids) <= 100 and all(valid_id(bid) for bid in ids), "题库列表无效")
        ids = list(dict.fromkeys(ids))
        for name in names:
            ids.append(str(make_bank({"name": name}, session)["_id"]))
        skipped = []
        for bid in ids:
            b = bank(bid, session)
            duplicate = db.question_bank_items.find_one({"bankId": b["_id"], "fingerprint": doc["fingerprint"],
                "templateId": {"$ne": doc["_id"]}, "active": True}, session=session)
            if b.get("isDefault") and duplicate:
                skipped.append(b["name"])
            else:
                add_item(bid, doc, session)
        return skipped

    def public(doc, session=None):
        result = {key: doc[key] for key in ("_id", "ownerId", "question", "sourceTemplateId", "createdAt", "deleted")}
        personal = entry(doc, session)
        result["deleted"] = bool(personal and personal["removed"])
        result["readable"] = True
        bids = [b["_id"] for b in db.question_banks.find({"ownerId": g.user["_id"], "deleted": False}, session=session)]
        result["bankIds"] = [r["bankId"] for r in db.question_bank_items.find({"templateId": doc["_id"], "bankId": {"$in": bids}, "active": True}, session=session)]
        if doc["ownerId"] == g.user["_id"]:
            result["source"] = doc["source"]
        return result

    @app.get("/api/templates")
    @login_required
    def list_templates():
        scope = request.args.get("scope", "mine")
        require(scope in ("all", "mine", "shared", "history"), "模板范围无效")
        removed = [r["templateId"] for r in db.template_entries.find({"userId": g.user["_id"], "removed": True})]
        if scope == "history":
            query = {"_id": {"$in": removed}}
        elif scope == "shared":
            ids = [r["templateId"] for r in db.question_shares.find({"userId": g.user["_id"], "active": True})]
            query = {"_id": {"$in": ids}, "deleted": False, "ownerId": {"$ne": g.user["_id"]}}
        elif scope == "all":
            ids = [r["templateId"] for r in db.question_shares.find({"userId": g.user["_id"], "active": True})]
            query = {"deleted": False, "$or": [{"ownerId": g.user["_id"]}, {"_id": {"$in": ids}}]}
        else:
            query = {"ownerId": g.user["_id"]}
        if scope != "history":
            query = {"$and": [query, {"_id": {"$nin": removed}}]}
        if request.args.get("bankId"):
            b = bank(request.args["bankId"])
            ids = [r["templateId"] for r in db.question_bank_items.find({"bankId": b["_id"], "active": True})]
            query = {"$and": [query, {"_id": {"$in": ids}}]}
        rows = []
        for t in db.question_templates.find(query).sort("createdAt", -1):
            rows.append(public(t) if accessible(t) else {"_id": t["_id"], "ownerId": t["ownerId"], "deleted": True,
                "readable": False, "accessReason": "当前没有来源模板的共享权限，无法查看正文或恢复；已有问卷副本保留。"})
        return reply({"templates": rows})

    @app.get("/api/templates/<tid>")
    @login_required
    def get_template(tid):
        return reply(public(template(tid, history=True)))

    @app.post("/api/templates")
    @login_required
    def save_template():
        data = body()
        request_id = data.get("requestId")
        require(isinstance(request_id, str) and 8 <= len(request_id) <= 100, "保存操作标识无效")
        request_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        def commit(session):
            previous = db.question_templates.find_one({"ownerId": g.user["_id"], "requestId": request_id}, session=session)
            if previous:
                require(previous["requestHash"] == request_hash, "同一保存标识不能用于不同请求", status=409)
                return {"template": public(previous, session), "replayed": True, "skippedBanks": previous.get("skippedBanks", [])}
            survey = owned(data.get("surveyId"), session)
            version_id = data.get("versionId")
            if version_id:
                version = db.survey_versions.find_one({"_id": oid(version_id), "surveyId": survey["_id"]}, session=session)
                require(version is not None, "发布版本不存在", status=404)
                questions = version["questions"]
            else:
                require(survey["status"] == "draft", "已发布问卷须指定发布版本")
                require(type(data.get("revision")) is int and data["revision"] == survey["revision"], "草稿已变化，请重新保存", status=409, code="edit_conflict")
                questions = survey["questions"]
            q = next((q for q in questions if q["_id"] == data.get("questionId")), None)
            require(q is not None, "题目不存在", status=404)
            payload = validate_template(q)
            saved = q.get("savedTemplateIds") or []
            parent = saved[-1] if saved else q.get("sourceTemplateId")
            fingerprint = signature(q)
            if parent:
                original = db.question_templates.find_one({"_id": oid(parent)}, session=session)
                require(original is not None, "原模板来源不存在", status=409)
                require(original["fingerprint"] != fingerprint, "内容未修改，请使用已有模板", status=409, code="unchanged_template")
            # Idempotent against double clicks even if a client generates a fresh key.
            for previous in db.question_templates.find({"ownerId": g.user["_id"], "source.surveyId": survey["_id"],
                    "source.questionId": q["_id"], "source.versionId": oid(version_id) if version_id else None,
                    "sourceTemplateId": parent, "fingerprint": fingerprint, "deleted": False}, session=session):
                skipped = classify(previous, data, session, use_default=True)
                return {"template": public(previous, session), "replayed": True, "skippedBanks": skipped}
            tid = ObjectId()
            source = {"surveyId": survey["_id"], "versionId": oid(version_id) if version_id else None,
                      "questionId": q["_id"], "revision": survey["revision"]}
            doc = {"_id": tid, "ownerId": g.user["_id"], "question": payload, "sourceTemplateId": parent,
                   "source": source, "fingerprint": fingerprint, "statsFingerprint": signature(q, True),
                   "createdAt": now(), "deleted": False, "requestId": request_id, "requestHash": request_hash}
            db.question_templates.insert_one(doc, session=session)
            collect(doc, session)
            db.template_sources.insert_one({"templateId": tid, **source, "statsFingerprint": signature(q, True), "createdAt": now()}, session=session)
            # Keep future edits linked when a template is first saved from the
            # current published version; the immutable published snapshot stays intact.
            if not version_id or survey.get("publishedVersionId") == oid(version_id):
                for item in survey["questions"]:
                    if item["_id"] == q["_id"] and signature(item) == fingerprint:
                        if not item.get("sourceTemplateId"):
                            item["sourceTemplateId"] = str(tid)
                        item["savedTemplateIds"] = list(dict.fromkeys((item.get("savedTemplateIds") or []) + [str(tid)]))
                db.surveys.update_one({"_id": survey["_id"]}, {"$set": {"questions": survey["questions"], "updatedAt": now()}, "$inc": {"revision": 1}}, session=session)
            skipped = classify(doc, data, session, use_default=True)
            db.question_templates.update_one({"_id": tid}, {"$set": {"skippedBanks": skipped}}, session=session)
            return {"template": public(doc, session), "replayed": False, "skippedBanks": skipped}
        result = write(commit)
        return reply(result, 200 if result["replayed"] else 201)

    @app.post("/api/templates/<tid>/use")
    @login_required
    def use_template(tid):
        data = body()
        def commit(session):
            doc = template(tid, session)
            survey = owned(data.get("surveyId"), session)
            require(survey["status"] == "draft", "请先关闭问卷再添加题目", status=409)
            require(type(data.get("revision")) is int and data["revision"] == survey["revision"], "草稿已变化，请重新载入", status=409, code="edit_conflict")
            q = copy.deepcopy(doc["question"])
            q.update(_id=str(ObjectId()), jumpRules=[], sourceTemplateId=str(doc["_id"]))
            for option in q["options"]:
                option["_id"] = str(ObjectId())
            db.surveys.update_one({"_id": survey["_id"]}, {"$push": {"questions": q}, "$inc": {"revision": 1}, "$set": {"updatedAt": now()}}, session=session)
            return {"question": q}
        return reply(write(commit), 201)

    @app.delete("/api/templates/<tid>")
    @login_required
    def delete_template(tid):
        def commit(session):
            doc = db.question_templates.find_one({"_id": oid(tid)}, session=session)
            require(doc is not None and (accessible(doc, session) or entry(doc, session)), "模板不存在或无权访问", status=404)
            db.template_entries.update_one({"templateId": doc["_id"], "userId": g.user["_id"]},
                {"$set": {"removed": True, "removedAt": now()}}, upsert=True, session=session)
            bids = [b["_id"] for b in db.question_banks.find({"ownerId": g.user["_id"]}, session=session)]
            db.question_bank_items.update_many({"templateId": doc["_id"], "bankId": {"$in": bids}},
                {"$set": {"active": False}}, session=session)
            return {}
        return reply(write(commit))

    @app.post("/api/templates/<tid>/restore")
    @login_required
    def restore_template(tid):
        def commit(session):
            doc = template(tid, session, history=True)
            skipped = []
            personal = entry(doc, session)
            if not personal or not personal["removed"]:
                return {"skippedBanks": skipped}
            db.template_entries.update_one({"_id": personal["_id"]}, {"$set": {"removed": False}}, session=session)
            for item in db.question_bank_items.find({"templateId": doc["_id"]}, session=session):
                b = db.question_banks.find_one({"_id": item["bankId"], "ownerId": g.user["_id"], "deleted": False}, session=session)
                if b:
                    duplicate = db.question_bank_items.find_one({"bankId": b["_id"], "active": True, "fingerprint": doc["fingerprint"]}, session=session)
                    if duplicate:
                        skipped.append(b["name"])
                    else:
                        db.question_bank_items.update_one({"_id": item["_id"]}, {"$set": {"active": True}}, session=session)
            return {"skippedBanks": skipped}
        return reply(write(commit))

    @app.get("/api/templates/<tid>/history")
    @login_required
    def history(tid):
        start = template(tid, history=True)
        nodes, seen = [], set()
        pending = [start]
        # Traverse ancestors and descendants; unreadable nodes reveal no title/author.
        while pending:
            doc = pending.pop(0)
            key = str(doc["_id"])
            if key in seen:
                continue
            seen.add(key)
            mine = doc["ownerId"] == g.user["_id"]
            readable = accessible(doc)
            personal = entry(doc)
            nodes.append({"_id": doc["_id"], "sourceTemplateId": doc["sourceTemplateId"], "readable": readable,
                          **({"question": doc["question"], "deleted": bool(personal and personal["removed"]), "mine": mine,
                              "createdAt": doc["createdAt"], "canRestore": bool(personal and personal["removed"])} if readable else
                             {"accessReason": "此来源模板属于其他用户，目前未共享给你或共享已撤销。来源关系保留，不影响你的题目副本和派生模板。"})})
            if doc.get("sourceTemplateId"):
                parent = db.question_templates.find_one({"_id": oid(doc["sourceTemplateId"])})
                if parent:
                    pending.append(parent)
            # Do not disclose other users' private descendants or the fact they exist.
            visible = {"$or": [{"ownerId": g.user["_id"]}, {"_id": {"$in": [r["templateId"] for r in db.question_shares.find({"userId": g.user["_id"], "active": True})]}, "deleted": False}]}
            pending.extend(db.question_templates.find({"$and": [{"sourceTemplateId": key}, visible]}))
        return reply({"nodes": nodes})

    @app.get("/api/users/lookup")
    @login_required
    def lookup_user():
        doc = db.users.find_one({"username": request.args.get("username", "")}, {"username": 1})
        require(doc is not None, "用户不存在", status=404)
        return reply({"user": doc})

    @app.get("/api/templates/<tid>/shares")
    @login_required
    def shares(tid):
        doc = template(tid, owner=True, history=True)
        rows = []
        for r in db.question_shares.find({"templateId": doc["_id"], "active": True}):
            user = db.users.find_one({"_id": r["userId"]}, {"username": 1})
            if user:
                rows.append(user)
        return reply({"users": rows})

    @app.post("/api/templates/<tid>/shares")
    @login_required
    def grant_share(tid):
        data = body()
        require(isinstance(data.get("username"), str), "请输入完整用户名")
        def commit(session):
            doc = template(tid, session, owner=True, history=True)
            user = db.users.find_one({"username": data.get("username")}, session=session)
            require(user is not None and str(user["_id"]) == data.get("userId"), "用户不存在或确认对象不一致", status=404)
            require(user["_id"] != g.user["_id"], "无需共享给自己")
            db.question_shares.update_one({"templateId": doc["_id"], "userId": user["_id"]},
                {"$set": {"active": True, "grantedBy": g.user["_id"], "updatedAt": now()}}, upsert=True, session=session)
            existed = entry(doc, session, user["_id"])
            collect(doc, session, user["_id"])
            if not existed:
                b = default_bank(session, user["_id"])
                duplicate = db.question_bank_items.find_one({"bankId": b["_id"], "fingerprint": doc["fingerprint"], "active": True}, session=session)
                if not duplicate:
                    db.question_bank_items.insert_one({"bankId": b["_id"], "templateId": doc["_id"], "fingerprint": doc["fingerprint"],
                        "active": True, "createdAt": now()}, session=session)
            return {}
        return reply(write(commit))

    @app.delete("/api/templates/<tid>/shares/<uid>")
    @login_required
    def revoke_share(tid, uid):
        def commit(session):
            doc = template(tid, session, owner=True, history=True)
            db.question_shares.update_one({"templateId": doc["_id"], "userId": oid(uid)}, {"$set": {"active": False}}, session=session)
            db.question_bank_items.update_many({"templateId": doc["_id"], "bankId": {"$in": [b["_id"] for b in db.question_banks.find({"ownerId": oid(uid)}, session=session)]}}, {"$set": {"active": False}}, session=session)
            return {}
        return reply(write(commit))

    @app.get("/api/banks")
    @login_required
    def list_banks():
        def prepare(session):
            b = default_bank(session)
            if b.get("backfilled"):
                return
            # Give pre-existing unclassified entries a default category once;
            # do not undo a user's later explicit removal from that category.
            bids = [r["_id"] for r in db.question_banks.find({"ownerId": g.user["_id"], "deleted": False}, session=session)]
            removed = [r["templateId"] for r in db.template_entries.find({"userId": g.user["_id"], "removed": True}, session=session)]
            shared = [r["templateId"] for r in db.question_shares.find({"userId": g.user["_id"], "active": True}, session=session)]
            for doc in db.question_templates.find({"_id": {"$nin": removed}, "$or": [{"ownerId": g.user["_id"]}, {"_id": {"$in": shared}}]}, session=session):
                personal = entry(doc, session)
                if personal and personal.get("classificationChosen"):
                    continue
                if not db.question_bank_items.find_one({"templateId": doc["_id"], "bankId": {"$in": bids}, "active": True}, session=session):
                    classify(doc, {"bankIds": [str(b["_id"])]}, session)
            db.question_banks.update_one({"_id": b["_id"]}, {"$set": {"backfilled": True}}, session=session)
        write(prepare)
        return reply({"banks": list(db.question_banks.find({"ownerId": g.user["_id"], "deleted": False}).sort("createdAt", 1))})

    @app.post("/api/banks")
    @login_required
    def create_bank():
        data = body()
        doc = write(lambda session: make_bank(data, session))
        return reply(doc, 201)

    @app.put("/api/banks/<bid>")
    @login_required
    def update_bank(bid):
        data = body()
        require(isinstance(data.get("name"), str) and bool(data["name"].strip()) and isinstance(data.get("description", ""), str), "题库名称和说明无效")
        def commit(session):
            b = bank(bid, session)
            require(not b.get("isDefault"), "默认题库保留固定名称，无需编辑")
            require(data["name"].strip() not in ("默认题库", "全部题库"), "该名称用于系统题库")
            require(not db.question_banks.find_one({"ownerId": g.user["_id"], "name": data["name"].strip(), "deleted": False,
                "_id": {"$ne": b["_id"]}}, session=session), "已有同名题库", status=409)
            db.question_banks.update_one({"_id": b["_id"]}, {"$set": {"name": data["name"].strip(), "description": data.get("description", "")}}, session=session)
        write(commit)
        return reply()

    @app.delete("/api/banks/<bid>")
    @login_required
    def delete_bank(bid):
        def commit(session):
            b = bank(bid, session)
            require(not b.get("isDefault"), "默认题库不能删除，可以移出其中的模板")
            db.question_banks.update_one({"_id": b["_id"]}, {"$set": {"deleted": True}}, session=session)
            db.question_bank_items.update_many({"bankId": b["_id"]}, {"$set": {"active": False}}, session=session)
        write(commit)
        return reply()

    @app.post("/api/templates/<tid>/banks")
    @login_required
    def classify_template(tid):
        data = body()
        def commit(session):
            doc = template(tid, session)
            return {"skippedBanks": classify(doc, data, session)}
        return reply(write(commit))

    @app.post("/api/banks/<bid>/items")
    @login_required
    def put_bank_item(bid):
        data = body()
        def commit(session):
            doc = template(data.get("templateId"), session)
            add_item(bid, doc, session)
        write(commit)
        return reply()

    @app.delete("/api/banks/<bid>/items/<tid>")
    @login_required
    def remove_bank_item(bid, tid):
        def commit(session):
            b = bank(bid, session)
            db.question_bank_items.delete_one({"bankId": b["_id"], "templateId": oid(tid)}, session=session)
        write(commit)
        return reply()

    def instances(doc):
        tid = str(doc["_id"])
        sources = list(db.template_sources.find({"templateId": doc["_id"]}))
        surveys = list(db.surveys.find({"creatorId": g.user["_id"]}))
        for survey in surveys:
            versions = list(db.survey_versions.find({"surveyId": survey["_id"]}))
            for version in [None] + versions:
                container = version or survey
                for q in container["questions"]:
                    links = tid == q.get("sourceTemplateId") or tid in (q.get("savedTemplateIds") or [])
                    for source in sources:
                        if source["surveyId"] == survey["_id"] and source["questionId"] == q["_id"]:
                            if source["versionId"] is not None:
                                links |= version is not None and source["versionId"] == version["_id"]
                            else:
                                # Future snapshots retain bindings; never retroactively link older snapshots.
                                links |= version is None
                    if links:
                        yield survey, version, q

    @app.get("/api/templates/<tid>/usage")
    @login_required
    def usage(tid):
        doc = template(tid, history=True)
        surveys = {}
        for survey, version, q in instances(doc):
            key = str(survey["_id"])
            row = surveys.setdefault(key, {"surveyId": survey["_id"], "title": survey["title"], "status": survey["status"], "instances": []})
            row["instances"].append({"questionId": q["_id"], "versionId": version["_id"] if version else None,
                "versionNumber": version["versionNumber"] if version else None, "current": (version is None and survey["status"] == "draft") or (version is not None and version["_id"] == survey.get("publishedVersionId") and survey["status"] == "published"),
                "modified": signature(q) != doc["fingerprint"]})
        return reply({"surveyCount": len(surveys), "surveys": list(surveys.values())})

    @app.get("/api/templates/<tid>/stats")
    @login_required
    def template_stats(tid):
        doc = template(tid, history=True)
        from app import deadline
        start, end = deadline(request.args.get("from")), deadline(request.args.get("to"))
        require(not (start and end) or start <= end, "统计开始时间不能晚于结束时间")
        selected_survey = request.args.get("surveyId")
        selected_version = request.args.get("versionId")
        if selected_survey:
            owned(selected_survey)
        if selected_version:
            v = db.survey_versions.find_one({"_id": oid(selected_version)})
            require(v is not None, "发布版本不存在", status=404)
            owned(str(v["surveyId"]))
        matches = {}
        source_rows = []
        for survey, version, q in instances(doc):
            if version is None or signature(q, True) != doc["statsFingerprint"]:
                continue
            if selected_survey and str(survey["_id"]) != selected_survey:
                continue
            if selected_version and str(version["_id"]) != selected_version:
                continue
            matches.setdefault(version["_id"], {})[q["_id"]] = q
            source_rows.append({"surveyId": survey["_id"], "title": version["title"], "versionId": version["_id"], "versionNumber": version["versionNumber"], "questionId": q["_id"], "questionNumber": next(i+1 for i, item in enumerate(version["questions"]) if item["_id"] == q["_id"])})
        query = {"surveyVersionId": {"$in": list(matches)}}
        if start or end:
            query["submittedAt"] = {**({"$gte": start} if start else {}), **({"$lte": end} if end else {})}
        respondents, rows, option_users, counts = set(), [], {}, Counter()
        target = doc["question"]
        for response in db.responses.find(query):
            questions = matches[response["surveyVersionId"]]
            for answer in response["answers"]:
                q = questions.get(answer["questionId"])
                if not q:
                    continue
                respondents.add(response["respondentId"])
                value = answer["value"]
                if target["type"] in ("single", "multiple"):
                    positions = {o["_id"]: i for i, o in enumerate(q["options"])}
                    selected = [value] if target["type"] == "single" else value
                    for oid_value in selected:
                        index = positions[oid_value]
                        counts[index] += 1
                        option_users.setdefault(index, set()).add(response["respondentId"])
                rows.append({"value": value, "submittedAt": response["submittedAt"]})
        result = {"templateId": doc["_id"], "type": target["type"], "title": target["title"], "answerCount": len(rows), "respondentCount": len(respondents), "sources": source_rows}
        if target["type"] in ("single", "multiple"):
            result["options"] = [{"text": o["text"], "count": len(option_users.get(i, set())) if target["type"] == "single" else counts[i]} for i, o in enumerate(target["options"])]
        else:
            result["contents"] = rows
            if target["type"] == "number":
                values = [r["value"].to_decimal() for r in rows]
                with localcontext() as ctx:
                    ctx.prec = 50
                    result.update(average=exact_average(values),
                                  minimum=format(min(values), "f") if values else None, maximum=format(max(values), "f") if values else None,
                                  distribution=[{"value": format(v, "f"), "count": n, "percentage": format(Decimal(n) * 100 / len(values), ".2f")} for v, n in sorted(Counter(values).items())])
        return reply(result)
