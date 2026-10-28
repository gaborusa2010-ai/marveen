"""Test fixture for the mail gates (#718): the Google client chain split over two
statements. Never run by the tests; the gates only read it."""


def main(svc):
    m = svc.users().messages()
    m.send(userId="me", body={"raw": ""}).execute()
