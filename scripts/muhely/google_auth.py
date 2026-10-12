#!/usr/bin/env python3
"""
Google OAuth and REST helper, dependency free.

The fleet has no google-api-python-client installed and no venv worth building
for four REST calls, so this talks to the Google APIs over plain urllib. It
handles the installed-app OAuth dance, caches the refresh token, and exposes a
thin `call` helper for Gmail, Calendar and Drive.

Usage
-----
  google_auth.py url                     # print the consent URL to open
  google_auth.py exchange "<code|url>"   # finish auth; accepts the whole redirect URL
  google_auth.py status                  # who is authorised, and for what
  google_auth.py revoke                  # drop the local token (Google side stays)

  google_auth.py test                    # one read from each API, proves it works

Scopes are declared in SCOPES below. Changing them requires re-running the
consent flow; Google will not widen an existing token.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = os.path.join(REPO, "store")
CLIENT = os.path.join(STORE, ".gdocs-oauth-client.json")
TOKEN = os.path.join(STORE, ".google-oauth.json")

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
TOKENINFO_URI = "https://oauth2.googleapis.com/tokeninfo"
REDIRECT = "http://localhost"

# Least privilege that still does the job. gmail.readonly is deliberately not
# gmail.modify: reading enquiries does not require the power to delete mail.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    # gmail.modify: labels/filters/archive for the inbox clean-up (#462, owner tg 7373).
    # It cannot permanently delete mail; that needs the full mail.google.com scope.
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "openid", "email",
]

SCOPE_PLAIN = {
    "gmail.readonly": "beérkező levelek olvasása",
    "gmail.modify": "levelek címkézése, archiválása (végleges törlés nélkül)",
    "gmail.send": "levél küldése a nevedben",
    "calendar": "naptár olvasása és írása",
    "drive": "Drive fájlok olvasása és írása",
    "documents": "Google Docs szerkesztése",
}


def _client():
    if not os.path.exists(CLIENT):
        raise SystemExit(f"Nincs OAuth client fajl: {CLIENT}")
    d = json.load(open(CLIENT))
    d = d.get("installed") or d.get("web") or d
    return d["client_id"], d["client_secret"]


def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise SystemExit(f"Google elutasitotta ({e.code}):\n{detail}")


def cmd_url(_):
    cid, _secret = _client()
    q = urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
    })
    print(f"{AUTH_URI}?{q}")
    return 0


def cmd_exchange(a):
    raw = a.code.strip()
    # Accept the whole redirect URL, because that is what the browser leaves in
    # the address bar after localhost fails to load. Asking a person to extract
    # the code by hand is how this step goes wrong.
    if raw.startswith("http://") or raw.startswith("https://") or "code=" in raw:
        qs = urllib.parse.urlparse(raw).query or raw.split("?", 1)[-1]
        params = urllib.parse.parse_qs(qs)
        if "error" in params:
            raise SystemExit(f"A Google hibat adott vissza: {params['error'][0]}")
        if "code" not in params:
            raise SystemExit("Nem talaltam 'code' parametert a beillesztett szovegben.")
        raw = params["code"][0]

    cid, secret = _client()
    tok = _post(TOKEN_URI, {
        "code": raw, "client_id": cid, "client_secret": secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    })
    if "refresh_token" not in tok:
        raise SystemExit(
            "A Google nem adott refresh_token-t. Ez akkor tortenik, ha mar volt "
            "korabbi engedely. Vond vissza a myaccount.google.com/permissions oldalon, "
            "es futtasd ujra az 'url' lepest.")
    old = os.umask(0o077)
    try:
        with open(TOKEN, "w") as fh:
            json.dump({"refresh_token": tok["refresh_token"], "client_id": cid,
                       "client_secret": secret, "scopes": SCOPES}, fh, indent=2)
    finally:
        os.umask(old)
    os.chmod(TOKEN, 0o600)
    print("Token elmentve. Ellenorzes:")
    return cmd_status(a)


def access_token():
    if not os.path.exists(TOKEN):
        raise SystemExit("Nincs meg engedely. Futtasd: google_auth.py url")
    t = json.load(open(TOKEN))
    tok = _post(TOKEN_URI, {
        "client_id": t["client_id"], "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token",
    })
    return tok["access_token"]


def call(url, method="GET", params=None, body=None, token=None):
    """One authenticated REST call. Returns parsed JSON."""
    token = token or access_token()
    if params:
        # doseq is not optional: several Google endpoints take a repeated
        # parameter (metadataHeaders, fields). Without it a list is encoded as
        # its Python repr, the API ignores it, and the response comes back
        # silently empty rather than with an error.
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        raise SystemExit(f"API hiba {e.code} ({url.split('?')[0]}):\n{detail}")


def granted_scopes(token=None):
    """What the token ACTUALLY carries, straight from Google.

    Never report the SCOPES constant as if it were the answer: the consent
    screen lets the user untick individual permissions, and a tool that claims
    a power it does not have will fail later, in front of a client.
    """
    token = token or access_token()
    info = call(TOKENINFO_URI, params={"access_token": token})
    return set((info.get("scope") or "").split())


def cmd_status(_):
    if not os.path.exists(TOKEN):
        print("Nincs engedely. Futtasd: google_auth.py url")
        return 1
    tok = access_token()
    who = call("https://www.googleapis.com/oauth2/v2/userinfo", token=tok)
    have = granted_scopes(tok)
    print(f"Fiok:  {who.get('email', 'ismeretlen')}")
    print("\nMEGADOTT jogok:")
    for s in SCOPES:
        key = s.rsplit("/", 1)[-1]
        if key in SCOPE_PLAIN and s in have:
            print(f"  + {SCOPE_PLAIN[key]}")
    missing = [s for s in SCOPES if s.rsplit("/", 1)[-1] in SCOPE_PLAIN and s not in have]
    if missing:
        print("\nKERT, DE NEM MEGADOTT jogok:")
        for s in missing:
            print(f"  - {SCOPE_PLAIN[s.rsplit('/', 1)[-1]]}")
        print("\n  Ezek a muveletek NEM fognak mukodni. Ha kellenek, a gazdanak ujra "
              "kell engedelyeznie, es a hozzajarulasi kepernyon minden pipat bekapcsolva "
              "hagyni.")
    print(f"\nBarmikor visszavonhato: https://myaccount.google.com/permissions")
    return 0


def require(scope):
    """Fail loudly before a call, not with an opaque 403 in the middle of a job."""
    if scope not in granted_scopes():
        raise SystemExit(
            f"Hianyzo jogosultsag: {scope}\n"
            f"A gazda nem adta meg ezt a jogot. Futtasd a 'status' parancsot, "
            f"es kerd meg hogy engedelyezze ujra.")


def cmd_test(_):
    tok = access_token()
    ok = []
    prof = call("https://gmail.googleapis.com/gmail/v1/users/me/profile", token=tok)
    ok.append(f"Gmail:    {prof.get('emailAddress')}, {prof.get('messagesTotal')} level")
    cals = call("https://www.googleapis.com/calendar/v3/users/me/calendarList",
                params={"maxResults": 10}, token=tok)
    names = [c.get("summary") for c in cals.get("items", [])]
    ok.append(f"Naptar:   {len(names)} naptar ({', '.join(n for n in names[:3] if n)})")
    dr = call("https://www.googleapis.com/drive/v3/files",
              params={"pageSize": 5, "fields": "files(name)"}, token=tok)
    ok.append(f"Drive:    elerheto, elso fajlok: "
              f"{', '.join(f.get('name', '') for f in dr.get('files', [])[:3]) or 'ures'}")
    for line in ok:
        print(line)
    return 0


def cmd_revoke(_):
    if not os.path.exists(TOKEN):
        print("Nincs mit visszavonni.")
        return 0
    t = json.load(open(TOKEN))
    try:
        _post(REVOKE_URI, {"token": t["refresh_token"]})
    except SystemExit as e:
        print(f"A Google oldali visszavonas nem sikerult: {e}")
    os.remove(TOKEN)
    print("Helyi token torolve.")
    return 0


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("url").set_defaults(fn=cmd_url)
    p = sub.add_parser("exchange"); p.add_argument("code"); p.set_defaults(fn=cmd_exchange)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("test").set_defaults(fn=cmd_test)
    sub.add_parser("revoke").set_defaults(fn=cmd_revoke)
    a = ap.parse_args(argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
