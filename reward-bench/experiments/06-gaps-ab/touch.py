"""For each bug commit: functions it changes (at its parent), and repomap gaps for them."""
import ast,json,re,subprocess,sys,os
repo,clone,pkgprefix=sys.argv[1],sys.argv[2],sys.argv[3]  # pkgprefix e.g. src/
mapf=sys.argv[4] if len(sys.argv)>4 else None
sys.path.insert(0,'devtools/cli/src'); from repomap.query import gaps_of, rich_probes, BASIC
bugs=json.load(open(os.environ['S']+'/bugs.json'))[repo]
F=json.load(open(mapf))["functions"] if mapf else {}
PR=BASIC if os.environ.get("BASIC") else rich_probes(F)
def g(*a): return subprocess.run(['git','-C',clone,*a],capture_output=True,text=True).stdout
def funcs(text):
    out=[]
    def walk(n,pre):
        for c in ast.iter_child_nodes(n):
            if isinstance(c,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                q=pre+c.name
                if not isinstance(c,ast.ClassDef): out.append((q,c.lineno,c.end_lineno))
                walk(c,q+'.')
    walk(ast.parse(text),''); return out
for c in bugs:
    full=g('rev-parse',c).strip()
    subj=g('log','-1','--format=%s',full).strip()
    changed=set()
    for f in g('diff','--name-only',full+'^',full).split():
        if not f.startswith(pkgprefix) or not f.endswith('.py'): continue
        old=g('show',f'{full}^:{f}')
        if not old: changed.add(f+':<new file>'); continue
        lines=[]
        for h in re.finditer(r'^@@ -(\d+)(?:,(\d+))?',g('diff','-U0',full+'^',full,'--',f),re.M):
            s=int(h[1]); n=int(h[2] or 1); lines+=range(s,s+max(n,1))
        fs=funcs(old); rel=f[len(pkgprefix):] if pkgprefix.endswith('/') and pkgprefix!='' else f
        for L in lines:
            inner=[q for q,a,b in fs if a<=L<=b]
            changed.add(rel+':'+(max(inner,key=len) if inner else '<module>'))
    print(f'\n{c} {subj}  [{len(bugs[c])} tests]')
    for k in sorted(changed):
        if k in F:
            gp=[f"{a} never {'/'.join(x)}" for a,s in F[k]['args'].items() if a not in ("self","cls") and (x:=gaps_of(s,PR))]
            print(f'  {k}: '+('; '.join(gp) if gp else 'no gaps'))
        else: print(f'  {k}'+('  (not in map)' if F else ''))
