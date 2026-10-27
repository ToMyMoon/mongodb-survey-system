import json
import os
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    port = int(os.getenv("SURVEY_PORT", "5001"))
    with opener.open(f"http://127.0.0.1:{port}/api/auth/me", timeout=1) as response:
        data = json.load(response)
        if set(data) == {"csrf", "user"}:
            sys.exit(10)
except (OSError, ValueError):
    pass

