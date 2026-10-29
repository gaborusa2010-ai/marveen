"""Test fixture for the mail gates (#718): the SHAPE of an ad-hoc Gmail API sender.
Never run by the tests; the gates only read it. No credentials, no real address."""
import argparse
import base64
import json
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default="someone@example.invalid")
    ap.add_argument("--body", default="")
    args = ap.parse_args()
    raw = base64.urlsafe_b64encode(f"To: {args.to}\r\n\r\n{args.body}".encode()).decode()
    req = urllib.request.Request(f"{API}/messages/send", data=json.dumps({"raw": raw}).encode(), method="POST")
    urllib.request.urlopen(req)


if __name__ == "__main__":
    main()
