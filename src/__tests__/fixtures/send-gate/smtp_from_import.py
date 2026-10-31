"""Test fixture for the mail gates (#725): an SMTP sender in the from-import form.
Never run by the tests; the gates only read it. No credentials, no real address."""
from smtplib import SMTP

SMTP("localhost").sendmail("a@example.invalid", ["b@example.invalid"], "x")
