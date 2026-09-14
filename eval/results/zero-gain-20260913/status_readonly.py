"""Execute eval.status CLI unchanged except enforcing read-only SQLite connections."""
from pathlib import Path
import runpy
import sqlite3
import sys
original=sqlite3.connect
def connect(database,*args,**kwargs):
    kwargs['uri']=True
    return original(Path(database).resolve().as_uri()+'?mode=ro',*args,**kwargs)
sqlite3.connect=connect
sys.argv=['eval.status','--db','/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/campaign.sqlite']
runpy.run_module('eval.status',run_name='__main__')
