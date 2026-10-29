"""Test fixture for the mail gates (#718): a send through the Google API client
library (users().messages().send). Never run by the tests."""
from googleapiclient.discovery import build


def main(creds, raw):
    service = build("gmail", "v1", credentials=creds)
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
