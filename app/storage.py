import os, sqlite3, json, uuid, shutil, csv, zipfile, threading, time
from pathlib import Path
from datetime import datetime, timezone
from app.rendering import render, compress_pdf

ROOT=Path(os.getenv('CERTIFYFLOW_DATA_DIR','./data')).resolve()
UPLOADS=ROOT/'uploads'; OUTPUTS=ROOT/'outputs'; DB=ROOT/'certifyflow.sqlite3'
UPLOADS.mkdir(parents=True,exist_ok=True); OUTPUTS.mkdir(parents=True,exist_ok=True)
LOCK=threading.Lock()
def now(): return datetime.now(timezone.utc).isoformat()
def connect():
    c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA journal_mode=WAL'); return c
def init_db():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,source_file TEXT NOT NULL,file_type TEXT NOT NULL,page_index INTEGER NOT NULL DEFAULT 0,page_width REAL,page_height REAL,fields TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,template_id TEXT NOT NULL REFERENCES templates(id) ON DELETE CASCADE,status TEXT NOT NULL,total INTEGER NOT NULL,successful INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,skipped INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,completed_at TEXT,source_rows TEXT NOT NULL, mappings TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,row_num INTEGER NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,output_file TEXT,error TEXT,retry_count INTEGER NOT NULL DEFAULT 0,UNIQUE(job_id,row_num));''')

def template(tid):
    with connect() as c: r=c.execute('SELECT * FROM templates WHERE id=?',(tid,)).fetchone()
    if not r: return None
    d=dict(r); d['fields']=json.loads(d['fields']); return d
def job(jid,include_records=True):
    with connect() as c:
        r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r:return None
        d=dict(r)
        if include_records:
            d['records']=[dict(x) for x in c.execute('SELECT id,row_num,status,output_file,error,retry_count,data FROM records WHERE job_id=? ORDER BY row_num',(jid,))]
            for x in d['records']: x['data']=json.loads(x['data'])
        return d

def _safe_name(s):
    s=''.join(ch if ch.isalnum() or ch in '-_ ' else '_' for ch in str(s)).strip().replace(' ','_')
    return (s[:70] or 'participant')

def process_job(jid, retry=False):
    # SQLite claims and state transitions are atomic; a worker only processes queued records.
    with LOCK:
        with connect() as c:
            j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j:return
            if j['status']=='running':return
            t=c.execute('SELECT * FROM templates WHERE id=?',(j['template_id'],)).fetchone()
            if not t:return
            fields=json.loads(t['fields']); src=UPLOADS/t['source_file']; output_dir=OUTPUTS/jid; output_dir.mkdir(parents=True,exist_ok=True)
            if retry: c.execute("UPDATE records SET status='queued',error=NULL,retry_count=retry_count+1 WHERE job_id=? AND status='failed'",(jid,))
            c.execute("UPDATE jobs SET status='running',completed_at=NULL WHERE id=?",(jid,)); c.commit()
    while True:
        with connect() as c:
            rec=c.execute("SELECT * FROM records WHERE job_id=? AND status='queued' ORDER BY row_num LIMIT 1",(jid,)).fetchone()
            if not rec: break
            c.execute("UPDATE records SET status='running' WHERE id=? AND status='queued'",(rec['id'],)); c.commit()
        temp=None
        try:
            data=json.loads(rec['data']); display=data.get('participant_name') or data.get('name') or f"record_{rec['row_num']}"
            filename=f"{_safe_name(display)}_{rec['row_num']:04d}_{rec['id'][:8]}.pdf"
            final=output_dir/filename; temp=output_dir/(filename+'.partial')
            warnings=render(src,t['file_type'],temp,t['page_index'],fields,data)
            if warnings: raise ValueError('; '.join(warnings))
            compress_pdf(temp)
            temp.replace(final)
            with connect() as c: c.execute("UPDATE records SET status='success',output_file=?,error=NULL WHERE id=?",(filename,rec['id'])); c.commit()
        except Exception as e:
            if temp and temp.exists(): temp.unlink(missing_ok=True)
            with connect() as c: c.execute("UPDATE records SET status='failed',error=? WHERE id=?",(str(e)[:500],rec['id'])); c.commit()
    with connect() as c:
        counts={r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM records WHERE job_id=? GROUP BY status',(jid,))}
        queued=counts.get('queued',0)+counts.get('running',0)
        status='completed_with_errors' if counts.get('failed',0) else 'completed'
    # Publish terminal state only after the archive and report are atomically ready.
    if not queued: make_archive(jid)
    with connect() as c:
        c.execute('UPDATE jobs SET successful=?,failed=?,skipped=?,status=?,completed_at=? WHERE id=?',(counts.get('success',0),counts.get('failed',0),counts.get('skipped',0), 'running' if queued else status, None if queued else now(),jid)); c.commit()

def make_archive(jid):
    out=OUTPUTS/jid; out.mkdir(parents=True,exist_ok=True)
    with connect() as c: rows=c.execute("SELECT row_num,status,output_file,error FROM records WHERE job_id=? ORDER BY row_num",(jid,)).fetchall()
    ztemp=out/'certificates.zip.partial'; zfinal=out/'certificates.zip'
    with zipfile.ZipFile(ztemp,'w',zipfile.ZIP_DEFLATED) as z:
        for row in rows:
            if row['status']=='success' and row['output_file']:
                p=out/row['output_file']
                if p.exists(): z.write(p,p.name)
    ztemp.replace(zfinal)
    with (out/'report.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f); w.writerow(['row','status','output_filename','error'])
        w.writerows([[r['row_num'],r['status'],r['output_file'] or '',r['error'] or ''] for r in rows])

def recover_jobs():
    # Mark work interrupted by process death as queued so the persistent queue can resume.
    with connect() as c:
        c.execute("UPDATE records SET status='queued' WHERE status='running'")
        ids=[r['id'] for r in c.execute("SELECT id FROM jobs WHERE status='running'")]
        c.execute("UPDATE jobs SET status='queued',completed_at=NULL WHERE status='running'"); c.commit()
    for jid in ids: threading.Thread(target=process_job,args=(jid,),daemon=True).start()
