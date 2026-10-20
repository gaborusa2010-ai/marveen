#!/usr/bin/env python3
"""Why a SHORTER session is cheaper, measured on this agent's own transcripts.

Every request re-reads the whole conversation so far, so a session's token
cost grows with the SQUARE of its length. This script measures the two inputs
that decide the trade-off (the fixed preamble P, and the per-request growth g)
and then models the total weekly cost at several session lengths.

It is read-only. Nothing leaves the machine.

Context, 2026-09-23: the fleet has no compaction and no resume to improve. Every
agent is launched with --channels, and a --channels resume does not re-init the
plugin MCP server (src/web/agent-process.ts:1845), so --continue is deliberately
off; the context-guard restarts with { fresh: true } on top of that. A restart
therefore already discards everything, which leaves session LENGTH as the only
lever we actually hold. The knob is store/context-guard.json (actPct, limitTokens).

The model assumes linear growth and fungible work across sessions, and H (the
extra requests a restart costs: writing the handoff, re-orienting) is a guess,
so it is swept. What the model CANNOT price is continuity: a shorter session
loses the thread more often, and that cost is the owner's call, not this
script's.
"""
import json, glob, os, re, datetime, statistics
def project_slug(path):
    return re.sub(r'[^A-Za-z0-9]','-',path)
REPO=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D=os.path.join(REPO,'.channels-config','projects',project_slug(REPO))
cut=datetime.datetime.now().timestamp()-7*86400
sessions=[]
for f in glob.glob(os.path.join(D,'**','*.jsonl'),recursive=True):
    if os.path.getmtime(f)<cut: continue
    sizes=[]
    for line in open(f,encoding='utf-8',errors='replace'):
        line=line.strip()
        if not line: continue
        try: rec=json.loads(line)
        except Exception: continue
        m=rec.get('message')
        u=m.get('usage') if isinstance(m,dict) else None
        if isinstance(u,dict):
            n=u.get('cache_read_input_tokens',0)+u.get('cache_creation_input_tokens',0)+u.get('input_tokens',0)
            if n>0: sizes.append(n)
    if len(sizes)>=10: sessions.append(sizes)

print('sessionok (>=10 keres):',len(sessions))
Ns=[len(s) for s in sessions]
print('keres/session  median: %d  atlag: %.0f  max: %d'%(statistics.median(Ns),sum(Ns)/len(Ns),max(Ns)))
P=statistics.median([s[0] for s in sessions])
print('fix eloresz (P), median elso keres: %d token'%P)

# novekedesi utem: (utolso - elso) / (keresek-1), sessiononkent
gs=[(s[-1]-s[0])/(len(s)-1) for s in sessions if len(s)>1 and s[-1]>s[0]]
g=statistics.median(gs)
print('novekedes keresenkent (g), median: %.0f token  [n=%d session]'%(g,len(gs)))

R_total=sum(Ns)
print('osszes keres 7 nap alatt (>=10-es sessionokben): %d'%R_total)
measured=sum(sum(s) for s in sessions)
print('mert osszes kontextus-olvasas ezekben: %.2f Mrd token'%(measured/1e9))

print()
print('MODELL: egy session N kerese P + g*i-t olvas. Egy ujrainditas OVERHEAD kerest visz')
print('(handoff iras + tajekozodas). Az alabbi tabla ezt H-val jeloli.')
print()
for H in (3,6,10):
    print('  --- ha egy ujrainditas %d extra kerest visz (H=%d) ---'%(H,H))
    print('  %8s %10s %14s %8s'%('N','session','ossz token','valtozas'))
    base=None
    for N in (50,100,200,400,800,1600):
        # a HASZNOS keresek szama fix: R_total - a mostani overhead
        cur_N=statistics.median(Ns)
        useful=R_total*(1-H/cur_N) if cur_N>H else R_total
        sess=useful/max(N-H,1)
        tokens=sess*(N*P + g*N*(N-1)/2)
        if base is None: base=tokens
        print('  %8d %10.0f %13.2fMrd %7.0f%%'%(N,sess,tokens/1e9,100*tokens/base))
    print()
