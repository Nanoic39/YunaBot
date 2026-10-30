"""社交评论持久队列：读评论、审核草稿、一次执行、核验回执。

执行器必须在点击发送前 claim；超时/崩溃产生 unknown，不盲目重发。
只有真实评论回复验收通过的平台，才能发布首帖。
"""
from __future__ import annotations
import hashlib,json,sqlite3,time,uuid
from pathlib import Path
from contextlib import contextmanager

class SocialError(ValueError):pass

class SocialStore:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
        with self.db() as d:d.executescript('''
        CREATE TABLE IF NOT EXISTS social_posts(id TEXT PRIMARY KEY,platform TEXT,external_id TEXT UNIQUE,title TEXT,account TEXT,enabled INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS social_comments(id TEXT PRIMARY KEY,post_id TEXT,external_id TEXT,author TEXT,body TEXT,is_bot INTEGER,created REAL,UNIQUE(post_id,external_id));
        CREATE TABLE IF NOT EXISTS social_replies(id TEXT PRIMARY KEY,comment_id TEXT UNIQUE,body TEXT,state TEXT,created REAL,started REAL,evidence TEXT,approval TEXT);
        CREATE TABLE IF NOT EXISTS social_review_dispatch(reply_id TEXT PRIMARY KEY,body_hash TEXT,aid_id TEXT);
        CREATE TABLE IF NOT EXISTS social_revision_requests(reply_id TEXT PRIMARY KEY,feedback TEXT,actor TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS social_probes(platform TEXT PRIMARY KEY,reply_id TEXT,verified REAL);
        ''')
    @contextmanager
    def db(self):
        d=sqlite3.connect(self.path,timeout=10,isolation_level=None);d.row_factory=sqlite3.Row
        try:
            yield d
        except BaseException:
            if d.in_transaction:d.rollback()
            raise
        finally:d.close()
    def register(self,platform,external_id,title,account):
        if platform!='xiaoheihe' or not all(isinstance(x,str) and 0<len(x)<=300 for x in (external_id,title,account)):
            raise SocialError('平台、帖子或账号标识无效')
        with self.db() as d:
            old=d.execute('SELECT * FROM social_posts WHERE external_id=?',(external_id,)).fetchone()
            if old:return dict(old)
            pid=uuid.uuid4().hex;d.execute('INSERT INTO social_posts VALUES(?,?,?,?,?,0)',(pid,platform,external_id,title,account))
            return dict(d.execute('SELECT * FROM social_posts WHERE id=?',(pid,)).fetchone())
    def ingest(self,post_id,external_id,author,body,is_bot=False):
        if not all(isinstance(x,str) and x and len(x)<=2000 for x in (external_id,author,body)) or type(is_bot)!=bool:
            raise SocialError('评论内容无效')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            p=d.execute('SELECT * FROM social_posts WHERE id=?',(post_id,)).fetchone()
            if not p:raise SocialError('帖子未登记')
            if author==p['account']:raise SocialError('忽略自己的评论')
            old=d.execute('SELECT * FROM social_comments WHERE post_id=? AND external_id=?',(post_id,external_id)).fetchone()
            if old:
                if old['body']!=body or old['author']!=author:raise SocialError('评论发生变化，需重新核验')
                d.commit();return dict(old)
            cid=uuid.uuid4().hex;d.execute('INSERT INTO social_comments VALUES(?,?,?,?,?,?,?)',(cid,post_id,external_id,author,body,int(is_bot),self.clock()));d.commit()
            return dict(d.execute('SELECT * FROM social_comments WHERE id=?',(cid,)).fetchone())
    def draft(self,cid,body):
        if not isinstance(body,str) or not 1<=len(body)<=300:raise SocialError('回复长度应为1到300字')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            c=d.execute('SELECT * FROM social_comments WHERE id=?',(cid,)).fetchone()
            if not c:raise SocialError('评论不存在')
            old=d.execute('SELECT * FROM social_replies WHERE comment_id=?',(cid,)).fetchone()
            if old:d.commit();return dict(old)
            if c['is_bot']:
                count=d.execute("SELECT count(*) FROM social_replies r JOIN social_comments c ON c.id=r.comment_id WHERE c.post_id=? AND c.author=? AND r.state IN ('sending','sent','unknown')",(c['post_id'],c['author'])).fetchone()[0]
                if count>=2:body='我先不继续这段自动对话啦，避免双方反复触发消息；有新的人工话题时再聊。'
                # 不@、不提对方名称，避免自动账号再次被召回。
                body=body.replace('@','').replace(c['author'],'对方')
            rid=uuid.uuid4().hex;d.execute('INSERT INTO social_replies VALUES(?,?,?,?,?,?,?,?)',(rid,cid,body,'pending',self.clock(),None,'',''));d.commit()
            return self.reply(rid)
    def reply(self,rid):
        with self.db() as d:
            r=d.execute('''SELECT r.*,c.post_id,c.author,c.body comment_body,p.platform,p.external_id post_external_id,p.title post_title,p.account
            FROM social_replies r JOIN social_comments c ON c.id=r.comment_id JOIN social_posts p ON p.id=c.post_id WHERE r.id=?''',(rid,)).fetchone()
            if not r:raise SocialError('回复不存在')
            return dict(r)
    def request_revision(self,rid,feedback,actor):
        if not isinstance(feedback,str) or not 1<=len(feedback)<=2000:raise SocialError('修改意见无效')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            row=d.execute('SELECT state FROM social_replies WHERE id=?',(rid,)).fetchone()
            if not row or row['state'] not in ('pending','approved','revision_requested'):raise SocialError('此回复不能修改')
            d.execute("UPDATE social_replies SET state='revision_requested',approval='' WHERE id=?",(rid,))
            d.execute('INSERT OR REPLACE INTO social_revision_requests VALUES(?,?,?,?)',(rid,feedback,actor,'pending'))
            d.commit()
    def finish_revision(self,rid,body):
        if not isinstance(body,str) or not 1<=len(body)<=300:raise SocialError('改写内容无效')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            result=d.execute("UPDATE social_replies SET body=?,state='pending',approval='' WHERE id=? AND state='revision_requested'",(body,rid))
            if result.rowcount!=1:raise SocialError('修改请求已失效')
            d.execute("UPDATE social_revision_requests SET status='done' WHERE reply_id=?",(rid,));d.commit()

    def approve(self,rid,body_hash,actor):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            r=d.execute('SELECT * FROM social_replies WHERE id=?',(rid,)).fetchone()
            if not r or r['state']!='pending':raise SocialError('草稿已处理')
            if hashlib.sha256(r['body'].encode()).hexdigest()!=body_hash:raise SocialError('草稿已变更')
            d.execute("UPDATE social_replies SET state='approved',approval=? WHERE id=?",(actor,rid));d.commit()
    def reject(self,rid):
        with self.db() as d:d.execute("UPDATE social_replies SET state='rejected' WHERE id=? AND state IN ('pending','approved')",(rid,))
    def claim(self,rid):
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            r=d.execute('SELECT state FROM social_replies WHERE id=?',(rid,)).fetchone()
            if not r or r['state']!='approved':raise SocialError('回复未审核或已经执行')
            # 北京时间每日最多3条，unknown也占用，防止反复重试。
            day=int((self.clock()+28800)//86400)*86400-28800
            n=d.execute("SELECT count(*) FROM social_replies WHERE started>=? AND state IN ('sending','sent','unknown')",(day,)).fetchone()[0]
            if n>=3:raise SocialError('今日评论回复达到3条上限')
            d.execute("UPDATE social_replies SET state='sending',started=? WHERE id=?",(self.clock(),rid));d.commit()
        return self.reply(rid)
    def verified(self,rid,visible_texts,evidence,post_external_id):
        r=self.reply(rid)
        # 输入框内容不能当发送成功；调用方只传非EditText的可见节点文本。
        if r['state']!='sending' or r['post_external_id']!=post_external_id or r['body'] not in visible_texts or r['account'] not in visible_texts or not evidence:
            raise SocialError('缺少同帖、账号、已发回复的可见证据')
        with self.db() as d:
            d.execute('BEGIN IMMEDIATE')
            d.execute("UPDATE social_replies SET state='sent',evidence=? WHERE id=? AND state='sending'",(evidence,rid))
            d.execute('INSERT OR REPLACE INTO social_probes VALUES(?,?,?)',(r['platform'],rid,self.clock()));d.commit()
    def fail_unknown(self,rid):
        with self.db() as d:d.execute("UPDATE social_replies SET state='unknown' WHERE id=? AND state='sending'",(rid,))
    def ready(self,platform):
        with self.db() as d:return d.execute('SELECT 1 FROM social_probes WHERE platform=?',(platform,)).fetchone() is not None
    def snapshot(self):
        with self.db() as d:
            d.execute("UPDATE social_replies SET state='unknown' WHERE state='sending' AND started<?",(self.clock()-900,))
            return {'posts':[dict(r) for r in d.execute('SELECT * FROM social_posts')],
                    'replies':[dict(r) for r in d.execute('SELECT id,comment_id,state,created FROM social_replies ORDER BY created DESC LIMIT 100')],
                    'reply_ready':self.ready('xiaoheihe')}

social_store=SocialStore(Path(__file__).resolve().parents[2]/'data/agent/social.sqlite3')
