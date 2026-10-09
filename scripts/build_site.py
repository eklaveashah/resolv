"""Build only public assets and dated exchange snapshots for GitHub Pages."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Backend'))
from resolv_engine.live import Feeds


def build(out=None):
    out=Path(out or ROOT/'_site')
    out.mkdir(parents=True,exist_ok=True)
    for name in ['index.html','methodology.html','terminal.html','404.html','style.css','script.js','CNAME','.nojekyll','robots.txt','sitemap.xml','_headers']:
        shutil.copy2(ROOT/name,out/name)
    cache=ROOT/'.cache/feed.json'
    feeds=Feeds()
    if cache.exists():
        try:
            old=json.loads(cache.read_text())
            for key,entry in old.get('feeds',{}).items():
                if key in feeds.locks:
                    feeds.entries[key]={**entry,'checked':0}
        except (ValueError,TypeError):
            pass
    payload=feeds.payload()
    payload['feeds']={key:feeds.source(key) for key in feeds.locks}
    payload['generatedAt']=int(time.time()*1000)
    payload['refreshMode']='scheduled polling; approximately ten minutes, may be delayed'
    (out/'data').mkdir(exist_ok=True)
    text=json.dumps(payload,ensure_ascii=False,allow_nan=False)
    (out/'data/feed.json').write_text(text)
    cache.parent.mkdir(exist_ok=True);cache.write_text(text)
    print(json.dumps({k:{'status':v['status'],'rows':len(v['items']),'ageSeconds':v['ageSeconds']} for k,v in payload['feeds'].items()},indent=2))
    return out

if __name__=='__main__':build()
