#!/usr/bin/env python3
"""Megkeresi egy Telegram MarkdownV2 uzenetben a NEM escapelt specialis karaktereket.

    python3 scripts/tg-mv2-check.py uzenet.txt      # 0 talalat = kuldheto

MIERT LETEZIK: 2026-09-06 21:02, ket egymas utani sikertelen kuldes ugyanazzal a
hibaval ("Character '.' is reserved"). A Telegram csak ANNYIT mond, hogy egy pont
gond, azt NEM, hogy hol -- es a szemmel valo atnezes epp azt a helyet hagyta ki,
ahol a pont egy *bold* jelolon BELUL allt (`*Esti zaras.*`). Ket kor talalgatas
utan ez a hat sor egy masodperc alatt megtalalta.

Amit szandekosan NEM ellenoriz: a backtickek kozotti code span belsejet (ott nem
kell escape) es magat a `*` jelolot. Tehat nem formazas-ellenorzo, csak azt a
kerdest valaszolja meg, amibe a kuldes bele szokott bukni.
"""
import sys
t = open(sys.argv[1], encoding='utf-8').read()
SPECIAL = set('_[]()~>#+-=|{}.!')
out, i, n = [], 0, len(t)
while i < n:
    ch = t[i]
    if ch == '\\':
        i += 2; continue
    if ch == '`':                       # code span: belul nem kell escape
        j = t.find('`', i+1)
        i = (j+1) if j != -1 else n; continue
    if ch == '*':                       # a csillag maga a jelolo
        i += 1; continue
    if ch in SPECIAL:
        out.append((i, ch, t[max(0,i-35):i+15].replace('\n','\\n')))
    i += 1
print("nem escapelt specialis karakter:", len(out))
for pos, ch, ctx in out:
    print(f"  poz {pos}: '{ch}'  ...{ctx}...")
