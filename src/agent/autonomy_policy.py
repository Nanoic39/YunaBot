"""Persistent autonomy preferences and per-site backoff; no credentials stored."""
import json,os,threading,time
from pathlib import Path

class AutonomyPolicy:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock()
        self.data={'quiet':True,'notify_payments':False,'social_accounts':{'xiaoheihe':os.environ.get('XIAOHEIHE_ACCOUNT','')},'site_blocks':{}}
        try:self.data.update(json.loads(self.path.read_text()))
        except (OSError,ValueError,TypeError):pass
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(self.data,ensure_ascii=False,indent=1));tmp.chmod(0o600);tmp.replace(self.path)
    def snapshot(self):
        with self.lock:return json.loads(json.dumps(self.data))
    def allow_aid(self,kind):
        return not self.data['quiet'] or kind in {'payment','sms','phone','password'}
    def block(self,host,reason,seconds=21600):
        if not host:return
        with self.lock:
            self.data['site_blocks'][host]={'until':self.clock()+seconds,'reason':str(reason)[:200]};self.save()
    def blocked(self,host):
        if not host:return None
        for domain,row in self.data['site_blocks'].items():
            if (host==domain or host.endswith('.'+domain)) and row.get('until',0)>self.clock():return row
        return None

policy=AutonomyPolicy(Path(__file__).resolve().parents[2]/'data/agent/autonomy_policy.json')
