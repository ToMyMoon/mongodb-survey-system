"""MongoDB validators and indexes, created before accepting traffic."""
from pymongo import ASCENDING


def initialize_database(db):
    schemas = {
        "users": {"bsonType": "object", "required": ["username", "passwordHash", "createdAt"],
                  "properties": {"username": {"bsonType": "string", "maxLength": 15}, "passwordHash": {"bsonType": "string"}, "createdAt": {"bsonType": "date"}}},
        "surveys": {"bsonType": "object", "required": ["creatorId", "status", "questions", "revision"],
                    "properties": {"creatorId": {"bsonType": "objectId"}, "status": {"enum": ["draft", "published"]}, "questions": {"bsonType": "array"}}},
        "survey_versions": {"bsonType": "object", "required": ["surveyId", "versionNumber", "questions", "publishedAt"],
                            "properties": {"surveyId": {"bsonType": "objectId"}, "questions": {"bsonType": "array"}}},
        "responses": {"bsonType": "object", "required": ["surveyId", "surveyVersionId", "respondentId", "submissionCode", "repeatRestricted", "isAnonymous", "answers", "submittedAt", "payloadHash"],
                      "properties": {"surveyId": {"bsonType": "objectId"}, "surveyVersionId": {"bsonType": "objectId"}, "respondentId": {"bsonType": "objectId"},
                                     "submissionCode": {"bsonType": "long"}, "repeatRestricted": {"bsonType": "bool"}, "isAnonymous": {"bsonType": "bool"},
                                     "answers": {"bsonType": "array"}, "submittedAt": {"bsonType": "date"}}},
        "sessions": {"bsonType": "object", "required": ["userId", "csrf", "expiresAt"],
                     "properties": {"userId": {"bsonType": "objectId"}, "expiresAt": {"bsonType": "date"}}},
    }
    schemas.update({
        "template_entries": {"bsonType": "object", "required": ["userId", "templateId", "removed"],
            "properties": {"userId": {"bsonType": "objectId"}, "templateId": {"bsonType": "objectId"}, "removed": {"bsonType": "bool"}}},
        "question_templates": {"bsonType": "object", "required": ["ownerId", "question", "fingerprint", "statsFingerprint", "deleted", "createdAt", "source", "requestId"],
            "properties": {"ownerId": {"bsonType": "objectId"}, "question": {"bsonType": "object"}, "deleted": {"bsonType": "bool"}}},
        "question_shares": {"bsonType": "object", "required": ["templateId", "userId", "active"],
            "properties": {"templateId": {"bsonType": "objectId"}, "userId": {"bsonType": "objectId"}, "active": {"bsonType": "bool"}}},
        "question_banks": {"bsonType": "object", "required": ["ownerId", "name", "description", "deleted"],
            "properties": {"ownerId": {"bsonType": "objectId"}, "name": {"bsonType": "string"}, "description": {"bsonType": "string"}, "deleted": {"bsonType": "bool"}}},
        "question_bank_items": {"bsonType": "object", "required": ["bankId", "templateId", "fingerprint", "active"],
            "properties": {"bankId": {"bsonType": "objectId"}, "templateId": {"bsonType": "objectId"}, "active": {"bsonType": "bool"}}},
        "template_sources": {"bsonType": "object", "required": ["templateId", "surveyId", "versionId", "questionId", "statsFingerprint"],
            "properties": {"templateId": {"bsonType": "objectId"}, "surveyId": {"bsonType": "objectId"}, "questionId": {"bsonType": "string"}}},
        "template_state": {"bsonType": "object", "required": ["revision"]},
    })
    existing = set(db.list_collection_names())
    for name, schema in schemas.items():
        validator = {"$jsonSchema": schema}
        if name in existing:
            db.command("collMod", name, validator=validator, validationLevel="strict")
        else:
            db.create_collection(name, validator=validator)
    db.users.create_index("username", unique=True, name="unique_username")
    db.surveys.create_index("creatorId")
    db.survey_versions.create_index([("surveyId", ASCENDING), ("versionNumber", ASCENDING)], unique=True)
    db.responses.create_index("submissionCode", unique=True, name="unique_submission_code")
    db.responses.create_index([("surveyId", 1), ("respondentId", 1)], unique=True,
                              partialFilterExpression={"repeatRestricted": True}, name="unique_single_response")
    db.responses.create_index([("surveyId", 1), ("surveyVersionId", 1)])
    db.sessions.create_index("expiresAt", expireAfterSeconds=0)
    db.question_templates.create_index([("ownerId", 1), ("deleted", 1), ("createdAt", -1)])
    db.question_templates.create_index([("ownerId", 1), ("requestId", 1)], unique=True)
    db.question_templates.create_index("sourceTemplateId")
    db.question_templates.create_index([("source.surveyId", 1), ("source.questionId", 1)])
    db.question_shares.create_index([("templateId", 1), ("userId", 1)], unique=True)
    db.question_shares.create_index([("userId", 1), ("active", 1)])
    db.template_entries.create_index([("userId", 1), ("templateId", 1)], unique=True)
    db.template_entries.create_index([("userId", 1), ("removed", 1)])
    db.question_banks.create_index([("ownerId", 1), ("deleted", 1)])
    db.question_banks.create_index("ownerId", unique=True, partialFilterExpression={"isDefault": True}, name="unique_default_bank")
    db.question_bank_items.create_index([("bankId", 1), ("templateId", 1)], unique=True)
    db.question_bank_items.create_index([("bankId", 1), ("fingerprint", 1)], unique=True,
        partialFilterExpression={"active": True}, name="unique_active_bank_content")
    db.question_bank_items.create_index("templateId")
    db.template_sources.create_index([("templateId", 1), ("surveyId", 1), ("versionId", 1), ("questionId", 1)], unique=True)
