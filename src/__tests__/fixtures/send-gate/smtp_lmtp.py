"""Test fixture for the mail gates (#725, review 3034): an LMTP client is an SMTP
subclass and sends the same way. Never run by the tests; the gates only read it."""
import smtplib

with smtplib.LMTP("localhost") as s:
    s.sendmail("nobody@example.invalid", ["nobody@example.invalid"], "x")
