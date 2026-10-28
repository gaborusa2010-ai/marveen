"""Test fixture for the mail gates (#718): Microsoft Graph send of a saved draft.
Never run by the tests; the gates only read it."""
import urllib.request

MSG_ID = "AAMkAD-example"
urllib.request.urlopen(urllib.request.Request(
    f"https://graph.microsoft.com/v1.0/me/messages/{MSG_ID}/send", data=b"", method="POST"))
