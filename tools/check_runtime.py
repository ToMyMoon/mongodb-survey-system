"""Probe only the isolated stage-two web service, bypassing machine proxies."""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last_error = None
for attempt in range(10):
    try:
        with opener.open("http://127.0.0.1:5001/api/auth/me", timeout=2) as response:
            assert set(json.load(response)) == {"user", "csrf"}
        with opener.open("http://127.0.0.1:5001/library", timeout=2) as response:
            assert "第二阶段" in response.read().decode("utf-8")
        with opener.open("http://127.0.0.1:5001/static/library.js", timeout=2) as response:
            served = response.read()
        assert hashlib.sha256(served).digest() == hashlib.sha256((ROOT / "static" / "library.js").read_bytes()).digest()
        print(json.dumps({"url": "http://127.0.0.1:5001/library", "api": "ok", "stage": 2,
                          "frontendMatchesDisk": True}))
        break
    except (OSError, AssertionError, ValueError) as error:
        last_error = error
        time.sleep(.5)
else:
    raise SystemExit(f"第二阶段服务核对未通过：{last_error}")
