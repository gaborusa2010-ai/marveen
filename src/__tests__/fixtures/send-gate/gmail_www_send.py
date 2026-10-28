"""Test fixture for the mail gates (#718): a Gmail API sender on the www.googleapis.com
host. Never run by the tests; the gates only read it. No credentials, no real address."""
import json
import urllib.request

API = "https://www.googleapis.com/gmail/v1/users/me"


def main():
    req = urllib.request.Request(f"{API}/messages/send", data=json.dumps({"raw": ""}).encode(), method="POST")
    urllib.request.urlopen(req)


if __name__ == "__main__":
    main()
