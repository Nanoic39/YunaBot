"""评论队列的重要状态通知；按回复、状态和接收人去重。"""
def pending_notices(store, recipient, can_operate):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS social_notices(reply_id TEXT,stage TEXT,recipient TEXT,PRIMARY KEY(reply_id,stage,recipient))')
        rows=db.execute("SELECT id,body,state FROM social_replies WHERE state IN ('approved','sent','unknown')").fetchall()
        result=[]
        for row in rows:
            state=row['state']
            if state=='approved' and can_operate:continue
            stage='paused' if state=='approved' else state
            if db.execute('SELECT 1 FROM social_notices WHERE reply_id=? AND stage=? AND recipient=?',(row['id'],stage,recipient)).fetchone():continue
            text={'paused':'这条回复你已经同意了，但电脑使用时间结束了，所以还没发出去。我先保留在队列里，恢复使用后继续，不会当成已经发布。',
                  'sent':'刚才那条回复已发出，也在帖子里确认看到了：',
                  'unknown':'刚才那条回复的发送结果暂时没能核实。我会先检查帖子，不会直接重发，避免重复。'}[stage]
            result.append({'reply_id':row['id'],'stage':stage,'text':text+'\n\n'+row['body']})
        return result

def delivered(store, recipient, notice):
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO social_notices VALUES(?,?,?)',(notice['reply_id'],notice['stage'],recipient))
