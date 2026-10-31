"""Test fixture for the mail gates (#725): the mail library imported, nothing sent.
Never run by the tests; the gates only read it."""
import smtplib

print(smtplib.SMTP_PORT)
