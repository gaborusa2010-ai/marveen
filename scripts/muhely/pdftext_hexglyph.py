import re,zlib,sys
b=open(sys.argv[1],'rb').read()
objs={}
for m in re.finditer(rb'(\d+) 0 obj(.*?)endobj',b,re.S): objs[int(m.group(1))]=m.group(2)
def stream(o):
    m=re.search(rb'stream\r?\n(.*?)endstream',o,re.S)
    if not m: return b''
    try: return zlib.decompress(m.group(1))
    except Exception: return m.group(1)
def cmap(data):
    mp={}
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar',data,re.S):
        for s,d in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',blk):
            mp[int(s,16)]=bytes.fromhex(d.decode()).decode('utf-16-be','replace')
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange',blk if False else data,re.S):
        for s,e,d in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>',blk):
            s,e,d=int(s,16),int(e,16),int(d,16)
            for i in range(s,e+1): mp[i]=chr(d+i-s)
    return mp
# font objects: find /ToUnicode ref
fontmap={}
for n,o in objs.items():
    if b'/Type/Font' in o.replace(b' ',b'') :
        m=re.search(rb'/ToUnicode\s+(\d+) 0 R',o)
        if m: fontmap[n]=cmap(stream(objs[int(m.group(1))]))
# resource names -> font obj
res={}
for n,o in objs.items():
    for name,ref in re.findall(rb'/(F\d+)\s+(\d+) 0 R',o):
        if int(ref) in fontmap: res[name.decode()]=fontmap[int(ref)]
out=[]
for n,o in sorted(objs.items()):
    d=stream(o)
    if b'BT' not in d: continue
    cur=None
    for tok in re.finditer(rb'/(F\d+)\s+[\d.]+\s+Tf|<([0-9A-Fa-f]+)>|(ET)|(T\*|Td|TD)',d):
        if tok.group(1): cur=res.get(tok.group(1).decode(),{})
        elif tok.group(2) and cur is not None:
            h=tok.group(2).decode(); w=2 if max(cur or {0:0})<256 else 4
            out.append(''.join(cur.get(int(h[i:i+w],16),'?') for i in range(0,len(h),w)))
        elif tok.group(3) or tok.group(4): out.append('\n')
txt=''.join(out)
print(re.sub(r'\n+','\n',txt))
