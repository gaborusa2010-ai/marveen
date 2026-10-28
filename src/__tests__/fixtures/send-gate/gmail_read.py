"""Test fixture for the mail gates (#718): a READ-ONLY Gmail API script (search and
get). The negative control: it must pass. Never run by the tests."""
import argparse
import urllib.parse
import urllib.request

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="")
    args = ap.parse_args()
    urllib.request.urlopen(f"{API}/messages?q={urllib.parse.quote(args.q)}")


if __name__ == "__main__":
    main()
