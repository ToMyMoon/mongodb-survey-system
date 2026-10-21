"""Start an isolated local replica set; never modifies an existing MongoDB service."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError

ROOT = Path(__file__).resolve().parent
URI = "mongodb://127.0.0.1:27018/?directConnection=true"


def main():
    binary = os.getenv("MONGOD_PATH") or shutil.which("mongod")
    if not binary:
        raise SystemExit("未找到 mongod，请安装 MongoDB 或设置 MONGOD_PATH。")
    data = ROOT / ".mongo"
    data.mkdir(exist_ok=True)
    direct = MongoClient(URI, serverSelectionTimeoutMS=700, connectTimeoutMS=700)
    try:
        direct.admin.command("ping")
    except PyMongoError:
        process = subprocess.Popen([binary, "--dbpath", str(data), "--logpath", str(data / "mongod.log"),
                                    "--logappend", "--port", "27018", "--bind_ip", "127.0.0.1", "--replSet", "survey-rs"],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (data / "mongod.pid").write_text(str(process.pid), encoding="utf-8")
        for _ in range(40):
            try:
                direct.admin.command("ping")
                break
            except PyMongoError:
                if process.poll() is not None:
                    raise SystemExit(f"MongoDB 启动失败，请查看 {data / 'mongod.log'}")
                time.sleep(.5)
        else:
            raise SystemExit("MongoDB 启动超时，请检查日志。")
    options = direct.admin.command("getCmdLineOpts")["parsed"]
    replication = options.get("replication", {})
    configured = replication.get("replSetName") or replication.get("replSet")
    if configured != "survey-rs" or Path(options.get("storage", {}).get("dbPath", "")).resolve() != data.resolve():
        raise SystemExit("27018 端口已有其他 MongoDB 实例，未修改该实例。")
    try:
        direct.admin.command("replSetGetStatus")
    except PyMongoError as exc:
        if getattr(exc, "code", None) != 94:
            raise
        direct.admin.command("replSetInitiate", {"_id": "survey-rs", "members": [{"_id": 0, "host": "127.0.0.1:27018"}]})
    for _ in range(60):
        if direct.admin.command("hello").get("isWritablePrimary"):
            print("MongoDB 已就绪：127.0.0.1:27018，副本集 survey-rs。")
            direct.close()
            return
        time.sleep(.5)
    raise SystemExit("副本集主节点选举超时。")


if __name__ == "__main__":
    main()
