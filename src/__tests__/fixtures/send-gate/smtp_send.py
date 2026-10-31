"""Test fixture for the mail gates (#725): the SHAPE of an ad-hoc SMTP sender.
Never run by the tests; the gates only read it. No credentials, no real address."""
import os, smtplib
from email.message import EmailMessage

msg = EmailMessage()
msg["To"] = "nobody@example.invalid"
msg.set_content("x")
with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "localhost"), 465) as s:
    s.send_message(msg)
