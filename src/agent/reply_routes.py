"""Durable, sender-bound task-message routing. Quoted prose is never authority."""
from contextlib import contextmanager
from pathlib import Path
import json,sqlite3,time

class ReplyRoutes:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.db() as d:d.execute('CREATE TABLE IF NOT EXISTS routes(account TEXT,recipient TEXT,message TEXT,kind TEXT,ref TEXT,created REAL,PRIMARY KEY(account,recipient,message))')
  self.path.chmod(0o600)
 @contextmanager
 def db(self):
  d=sqlite3.connect(self.path);d.row_factory=sqlite3.Row
  try:
   with d:yield d
  finally:d.close()
 def bind(self,account,recipient,message,kind,ref):
  if message is None:return
  with self.db() as d:d.execute('INSERT OR IGNORE INTO routes VALUES(?,?,?,?,?,?)',(str(account),str(recipient),str(message),kind,str(ref),time.time()))
 def find(self,account,recipient,message):
  if message is None:return None
  with self.db() as d:
   r=d.execute('SELECT * FROM routes WHERE account=? AND recipient=? AND message=?',(str(account),str(recipient),str(message))).fetchone();return dict(r) if r else None

def quoted_id(event):
 reply=getattr(event,'reply',None)
 if reply is not None and getattr(reply,'message_id',None) is not None:return str(reply.message_id)
 message=getattr(event,'original_message',None)
 if message is None:message=getattr(event,'message',[])
 return next((str(seg.data['id']) for seg in message if seg.type=='reply' and 'id' in seg.data),None)

reply_routes=ReplyRoutes(Path(__file__).resolve().parents[2]/'data/agent/reply_routes.sqlite3')
