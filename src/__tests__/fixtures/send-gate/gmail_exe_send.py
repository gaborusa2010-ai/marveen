#!/usr/bin/env python3
"""Test fixture for the mail gates (#718): a Gmail API sender run directly by its
path (shebang). Never run by the tests; the gates only read it."""
import json
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"
urllib.request.urlopen(urllib.request.Request(
    f"{API}/messages/send", data=json.dumps({"raw": ""}).encode(), method="POST"))
