import json,re,os,sys
E='reward-bench/experiments/04-ab-test'
src=open(E+'/ab.py').read()
ns={'json':json,'re':re,'sys':sys,'collections':__import__('collections')}
body=src.split('def final',1)[1].split('out = {}')[0]
ns['lab']=json.load(open(E+'/labels-heldout.json')); ns['ov']=json.load(open('reward-bench/experiments/02-scale-up/label-overrides.json'))
exec('def final'+body,ns)
res={}
for repo in ["python-dotenv","cattrs","werkzeug","pydantic-settings"]:
    d={}
    for r in ns['lab'][repo]:
        if ns['final'](repo,r)=="bug": d.setdefault(ns['commit'](repo,r),[]).append(r["test"])
    res[repo]=d; print(repo,len(d),list(d)[:12])
json.dump(res,open(os.environ['S']+'/bugs.json','w'),indent=1)
