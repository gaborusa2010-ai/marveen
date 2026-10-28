"""Test fixture for the mail gates (#718): Microsoft Graph reply to a message.
Never run by the tests; the gates only read it."""
import json
import urllib.request

URL = "https://graph.microsoft.com/v1.0/me/messages/AAMkAD-example/replyAll"
urllib.request.urlopen(urllib.request.Request(
    URL, data=json.dumps({"comment": "hello"}).encode(), method="POST"))
