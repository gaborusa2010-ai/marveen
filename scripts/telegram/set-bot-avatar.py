"""Bot profilkep beallitasa a Bot API-bol.

MERT TENY (2026-09-01): a setMyProfilePhoto metodus LETEZIK, tehat NEM kell BotFather.
DE a `photo` NEM egyszeru fajl-mezo, hanem egy InputProfilePhoto JSON, ami attach://-csal
hivatkozik a csatolt fajlra. Egyszeru fajl-mezovel a valasz "photo isn't specified" --
ami ugy hangzik, mintha a metodus nem letezne, holott csak a forma volt rossz.
"""
import json, os, sys, urllib.request, uuid

def token_of(agent):
    p = f'/home/gabor/marveen/agents/{agent}/.claude/channels/telegram/.env'
    if not os.path.exists(p): return None
    for line in open(p):
        if line.startswith('TELEGRAM_BOT_TOKEN='):
            return line.split('=', 1)[1].strip().strip('"').strip("'") or None
    return None

def post(tok, method, fields, files):
    b = uuid.uuid4().hex; parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, p in files.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; '
                     f'filename="{os.path.basename(p)}"\r\nContent-Type: image/png\r\n\r\n'.encode())
        parts.append(open(p, 'rb').read()); parts.append(b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/{method}',
        data=b''.join(parts), headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r: return json.load(r)
    except Exception as e:
        try: return json.load(e)
        except Exception: return {'ok': False, 'description': str(e)[:80]}

for arg in sys.argv[1:]:
    agent, img = arg.split('=')
    tok = token_of(agent)
    if not tok: print(f'  {agent:8} NINCS token'); continue
    if not os.path.isfile(img): print(f'  {agent:8} NINCS kep: {img}'); continue
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/bot{tok}/getMe', timeout=20) as r:
            uname = json.load(r).get('result', {}).get('username', '?')
    except Exception: uname = '?'
    r = post(tok, 'setMyProfilePhoto',
             {'photo': json.dumps({'type': 'static', 'photo': 'attach://pic'})}, {'pic': img})
    print(f'  {agent:8} @{uname:24} {os.path.basename(img):16} -> ok={r.get("ok")} {r.get("description","")[:50]}')
