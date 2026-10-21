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
