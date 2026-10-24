import os, uuid, json, shutil, threading, mimetypes, secrets, hashlib, hmac, time, logging, zipfile, re, urllib.request
from collections import defaultdict, deque
from functools import lru_cache
from contextlib import asynccontextmanager
from pathlib import Path
from io import BytesIO
from dotenv import load_dotenv
load_dotenv()
import pandas as pd
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pypdf import PdfReader
from app.schemas import ConfigUpdate
from app.storage import (ROOT,UPLOADS,OUTPUTS,init_db,connect,now,template,job,process_job,recover_jobs,make_archive)
from app.rendering import render

logger=logging.getLogger('certifyflow.auth')
APP_ENV=os.getenv('CERTIFYFLOW_ENV','development').lower()
SESSION_SECRET=os.getenv('CERTIFYFLOW_SESSION_SECRET','')
ADMIN_USERNAME=os.getenv('CERTIFYFLOW_ADMIN_USERNAME','')
ADMIN_PASSWORD=os.getenv('CERTIFYFLOW_ADMIN_PASSWORD','')
COOKIE_SECURE=os.getenv('CERTIFYFLOW_COOKIE_SECURE','true' if APP_ENV=='production' else 'false').lower() in {'1','true','yes'}
ALLOWED_HOSTS=[x.strip() for x in os.getenv('CERTIFYFLOW_ALLOWED_HOSTS','localhost,127.0.0.1,testserver').split(',') if x.strip()]
origins=[x.strip() for x in os.getenv('CERTIFYFLOW_CORS_ORIGINS','').split(',') if x.strip()]
MAX_BYTES=int(os.getenv('CERTIFYFLOW_MAX_UPLOAD_MB','25'))*1024*1024
MAX_SPREADSHEET_EXPANDED_BYTES=int(os.getenv('CERTIFYFLOW_MAX_SPREADSHEET_EXPANDED_MB','100'))*1024*1024
AUTH_FAILURES=defaultdict(deque)
AUTH_LOCK=threading.Lock()
AUTH_WINDOW_SECONDS=15*60
AUTH_MAX_FAILURES=8
PASSWORD_SALT=secrets.token_bytes(16)
PASSWORD_HASH=hashlib.scrypt(ADMIN_PASSWORD.encode('utf-8')[:1024],salt=PASSWORD_SALT,n=2**14,r=8,p=1,dklen=32)

@asynccontextmanager
async def lifespan(_app):
    if len(SESSION_SECRET)<32: raise RuntimeError('Set CERTIFYFLOW_SESSION_SECRET to a random value of at least 32 characters')
    if not ADMIN_USERNAME.strip() or len(ADMIN_USERNAME)>120: raise RuntimeError('Set CERTIFYFLOW_ADMIN_USERNAME before starting CertifyFlow')
    if len(ADMIN_PASSWORD)<16: raise RuntimeError('Set CERTIFYFLOW_ADMIN_PASSWORD to at least 16 characters')
    if APP_ENV=='production' and not COOKIE_SECURE: raise RuntimeError('CERTIFYFLOW_COOKIE_SECURE must be true in production')
    init_db(); recover_jobs()
    yield

app=FastAPI(title='CertifyFlow - Bulk Certificate Generator',version='0.1.0',docs_url='/docs' if os.getenv('CERTIFYFLOW_ENABLE_API_DOCS','false').lower()=='true' else None,redoc_url=None,openapi_url='/openapi.json' if os.getenv('CERTIFYFLOW_ENABLE_API_DOCS','false').lower()=='true' else None,lifespan=lifespan)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        path=request.url.path
        if path.startswith('/api/') and path not in {'/api/health','/api/auth/session','/api/auth/login'}:
            session=request.scope.get('session',{})
            if not session.get('user'):
                response=JSONResponse({'detail':'Sign in is required'},status_code=401)
            elif request.method not in {'GET','HEAD','OPTIONS'} and not hmac.compare_digest(str(request.headers.get('X-CSRF-Token','')),str(session.get('csrf_token',''))):
                response=JSONResponse({'detail':'CSRF token is missing or invalid'},status_code=403)
            else:
                response=await call_next(request)
        elif path=='/api/auth/login' and request.method not in {'GET','HEAD','OPTIONS'}:
            session=request.scope.get('session',{})
            if not session.get('csrf_token') or not hmac.compare_digest(str(request.headers.get('X-CSRF-Token','')),str(session.get('csrf_token',''))):
                response=JSONResponse({'detail':'Refresh the sign-in page and try again'},status_code=403)
            else:
                response=await call_next(request)
        else:
            response=await call_next(request)
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        if not path.startswith('/api/') and response.media_type!='application/pdf':
            response.headers.setdefault('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; frame-src blob:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'")
            response.headers.setdefault('X-Frame-Options','DENY')
        if APP_ENV=='production': response.headers.setdefault('Strict-Transport-Security','max-age=63072000; includeSubDomains')
        return response

# Middleware is added inner-to-outer so auth can read the session and CORS can
# answer preflight requests before auth checks run.
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware,secret_key=SESSION_SECRET or secrets.token_urlsafe(48),max_age=12*60*60,same_site='strict',https_only=COOKIE_SECURE,session_cookie='certifyflow_session')
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type','X-CSRF-Token'],allow_credentials=True)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=ALLOWED_HOSTS)

@app.get('/api/auth/session')
def auth_session(request:Request):
    if not request.session.get('csrf_token'): request.session['csrf_token']=secrets.token_urlsafe(32)
    return {'authenticated':bool(request.session.get('user')),'username':request.session.get('user'),'csrf_token':request.session['csrf_token']}

@app.post('/api/auth/login')
def auth_login(request:Request,body:dict=Body(...)):
    # Uvicorn only trusts forwarded headers from the configured proxy network.
    # request.client therefore avoids trusting a client-supplied X-Forwarded-For.
    address=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with AUTH_LOCK:
        failures=AUTH_FAILURES[address]
        while failures and now-failures[0]>=AUTH_WINDOW_SECONDS: failures.popleft()
        if len(failures)>=AUTH_MAX_FAILURES: raise HTTPException(429,'Too many sign-in attempts. Try again in 15 minutes.')
    username=str(body.get('username',''))[:120]; password=str(body.get('password',''))[:1024]
    candidate=hashlib.scrypt(password.encode('utf-8'),salt=PASSWORD_SALT,n=2**14,r=8,p=1,dklen=32)
    valid_user=hmac.compare_digest(username.encode('utf-8'),ADMIN_USERNAME.encode('utf-8'))
    valid_password=hmac.compare_digest(candidate,PASSWORD_HASH)
    if not (valid_user and valid_password):
        with AUTH_LOCK: AUTH_FAILURES[address].append(now)
        logger.warning('Rejected sign-in attempt from %s',address)
        raise HTTPException(401,'Username or password is incorrect')
    with AUTH_LOCK: AUTH_FAILURES.pop(address,None)
    csrf=secrets.token_urlsafe(32)
    request.session.clear();request.session.update({'user':ADMIN_USERNAME,'csrf_token':csrf})
    return {'authenticated':True,'username':ADMIN_USERNAME,'csrf_token':csrf}

@app.post('/api/auth/logout')
def auth_logout(request:Request):
    request.session.clear()
    return {'authenticated':False}

@app.get('/api/health')
def health(): return {'status':'ok'}

@app.get('/api/fonts')
@lru_cache(maxsize=1)
def google_font_catalog():
    fallback=['Alegreya','Archivo','Barlow','Bebas Neue','Bitter','DM Sans','DM Serif Display','Fira Sans','Lato','Lora','Merriweather','Montserrat','Nunito','Open Sans','Oswald','Playfair Display','Poppins','Roboto','Roboto Slab','Source Sans 3','Source Serif 4','Ubuntu','Work Sans']
    try:
        # Public catalog metadata, cached for this server process. The official Developer API is
        # also supported when GOOGLE_FONTS_API_KEY is configured.
        key=os.getenv('GOOGLE_FONTS_API_KEY')
        url=('https://www.googleapis.com/webfonts/v1/webfonts?sort=alpha&key='+key) if key else 'https://fonts.google.com/metadata/fonts'
        req=urllib.request.Request(url,headers={'User-Agent':'CertifyFlow/1.0'})
        with urllib.request.urlopen(req,timeout=5) as response: raw=response.read(8_000_000).decode('utf-8-sig')
        raw=re.sub(r'^\)\]\}\'\s*','',raw)
        payload=json.loads(raw)
        items=payload.get('items') or payload.get('familyMetadataList') or []
        names=sorted({str(x.get('family','')).strip() for x in items if x.get('family')})
        if names: return {'families':['Helvetica',*names],'source':'google-fonts','count':len(names)}
    except Exception:
        pass
    return {'families':['Helvetica',*fallback],'source':'fallback','count':len(fallback)}

def inspect_upload(path, ext):
    try:
        if ext=='.pdf':
            reader=PdfReader(str(path),strict=True)
            if reader.is_encrypted: raise ValueError('Encrypted PDFs are not supported')
            if not reader.pages or len(reader.pages)>100: raise ValueError('PDF must contain between 1 and 100 pages')
            p=reader.pages[0]; p.transfer_rotation_to_content() if p.get('/Rotate',0) else None
            return 'pdf',len(reader.pages),float(p.cropbox.width),float(p.cropbox.height)
        with Image.open(path) as im:
            expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG'}[ext]
            if im.format!=expected: raise ValueError('File signature does not match its extension')
            if im.width*im.height>50_000_000: raise ValueError('Image dimensions exceed the 50 megapixel limit')
            im.verify()
        with Image.open(path) as im: return 'image',1,float(im.width)*.75,float(im.height)*.75
    except Exception as e: raise HTTPException(400,f'Could not read template: {e}')

@app.post('/api/templates')
async def upload_template(file: UploadFile=File(...)):
    ext=Path(file.filename or '').suffix.lower()
    if ext not in {'.pdf','.png','.jpg','.jpeg'}: raise HTTPException(415,'Upload a PDF, PNG, JPG, or JPEG file')
    tid=str(uuid.uuid4()); dest=UPLOADS/(tid+ext); size=0
    try:
        with dest.open('wb') as out:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>MAX_BYTES: raise HTTPException(413,f'File exceeds {MAX_BYTES//1024//1024} MB limit')
                out.write(chunk)
        typ,pages,w,h=inspect_upload(dest,ext)
        # Validate signatures via the actual parser above; reject mismatched file extensions.
        with connect() as c:
            c.execute('INSERT INTO templates(id,name,source_file,file_type,page_index,page_width,page_height,fields,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,Path(file.filename).stem[:120],dest.name,typ,0,w,h,'[]',now(),now())); c.commit()
        return template(tid) | {'page_count':pages,'original_filename':Path(file.filename).name,'file_size':size,'preview_url':f'/api/templates/{tid}/preview-image'}
    except Exception:
        dest.unlink(missing_ok=True); raise

@app.get('/api/templates')
def list_templates():
    with connect() as c: rows=c.execute('SELECT id,name,file_type,page_index,page_width,page_height,created_at,updated_at FROM templates ORDER BY updated_at DESC').fetchall()
    return [dict(x) for x in rows]
@app.get('/api/templates/{tid}')
def get_template(tid):
    t=template(tid)
    if not t: raise HTTPException(404,'Template not found')
    t['page_count']=len(PdfReader(str(UPLOADS/t['source_file'])).pages) if t['file_type']=='pdf' else 1
    return t
@app.get('/api/templates/{tid}/preview-image')
def template_preview(tid,page:int=0):
    t=template(tid)
    if not t: raise HTTPException(404,'Template not found')
    p=UPLOADS/t['source_file']
    if t['file_type']=='pdf':
        import fitz
        try:
            doc=fitz.open(p); pix=doc[page].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
            im=Image.open(BytesIO(pix.tobytes('png'))).convert('RGB'); doc.close()
        except Exception as e: raise HTTPException(422,f'Could not render PDF preview: {e}')
    else: im=Image.open(p).convert('RGB')
    im.thumbnail((1400,1000)); b=BytesIO(); im.save(b,format='JPEG',quality=88); b.seek(0)
    return StreamingResponse(b,media_type='image/jpeg')
@app.put('/api/templates/{tid}/config')
def save_config(tid,body:ConfigUpdate):
    t=template(tid)
    if not t: raise HTTPException(404,'Template not found')
    if t['file_type']!='pdf' and body.page_index!=0: raise HTTPException(422,'Image templates only have page 1')
    width,height=t['page_width'],t['page_height']
    if t['file_type']=='pdf':
        reader=PdfReader(str(UPLOADS/t['source_file']))
        if body.page_index>=len(reader.pages): raise HTTPException(422,'Page index is out of range')
        page=reader.pages[body.page_index]
        if page.get('/Rotate',0): page.transfer_rotation_to_content()
        width,height=float(page.cropbox.width),float(page.cropbox.height)
    with connect() as c: c.execute('UPDATE templates SET name=?,page_index=?,page_width=?,page_height=?,fields=?,updated_at=? WHERE id=?',(body.name,body.page_index,width,height,json.dumps([x.model_dump() for x in body.fields]),now(),tid)); c.commit()
    return template(tid)
@app.delete('/api/templates')
def delete_all_templates():
    with connect() as c:
        templates=c.execute('SELECT source_file FROM templates').fetchall()
        active=c.execute("SELECT count(*) FROM jobs WHERE template_id IN (SELECT id FROM templates) AND status IN ('queued','running')").fetchone()[0]
        if active:
            raise HTTPException(409,'Wait for active generation jobs to finish before deleting all templates')
        job_ids=[r['id'] for r in c.execute('SELECT id FROM jobs WHERE template_id IN (SELECT id FROM templates)')]
        c.execute('DELETE FROM templates')
        c.commit()
    for row in templates:
        (UPLOADS/row['source_file']).unlink(missing_ok=True)
    for jid in job_ids:
        shutil.rmtree(OUTPUTS/jid,ignore_errors=True)
    return {'deleted':len(templates)}

@app.delete('/api/templates/{tid}')
def delete_template(tid):
    with connect() as c:
        r=c.execute('SELECT source_file FROM templates WHERE id=?',(tid,)).fetchone()
        if not r: raise HTTPException(404,'Template not found')
        active=c.execute("SELECT count(*) FROM jobs WHERE template_id=? AND status IN ('queued','running')",(tid,)).fetchone()[0]
        if active: raise HTTPException(409,'Wait for generation jobs to finish before deleting this template')
        job_ids=[x['id'] for x in c.execute('SELECT id FROM jobs WHERE template_id=?',(tid,))]
        c.execute('DELETE FROM templates WHERE id=?',(tid,)); c.commit()
    (UPLOADS/r['source_file']).unlink(missing_ok=True)
    for jid in job_ids: shutil.rmtree(OUTPUTS/jid,ignore_errors=True)
    return {'deleted':True}

@app.post('/api/templates/{tid}/preview')
def preview(tid,body:dict=Body(...)):
    t=template(tid)
    if not t: raise HTTPException(404,'Template not found')
    dest=ROOT/'preview'/f'{uuid.uuid4()}.pdf'; dest.parent.mkdir(exist_ok=True)
    warnings=render(UPLOADS/t['source_file'],t['file_type'],dest,t['page_index'],t['fields'],body.get('data',{}))
    return FileResponse(dest,media_type='application/pdf',filename='certificate-preview.pdf',headers={'X-Layout-Warnings':json.dumps(warnings)},background=BackgroundTask(dest.unlink,missing_ok=True))

async def read_spreadsheet(file):
    name=(file.filename or '').lower(); content=await file.read(MAX_BYTES+1)
    if len(content)>MAX_BYTES: raise HTTPException(413,'Spreadsheet exceeds upload limit')
    try:
        if name.endswith('.csv'): df=pd.read_csv(BytesIO(content),dtype=str,keep_default_na=False,encoding='utf-8-sig',skip_blank_lines=False)
        elif name.endswith(('.xlsx','.xlsm')):
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    members=archive.infolist()
                    expanded=sum(item.file_size for item in members)
                    if len(members)>10_000 or expanded>MAX_SPREADSHEET_EXPANDED_BYTES:
                        raise HTTPException(413,'Spreadsheet expands beyond the configured safety limit')
                    if any(item.file_size>0 and (item.compress_size==0 or item.file_size/item.compress_size>200) for item in members):
                        raise HTTPException(413,'Spreadsheet compression ratio exceeds the safety limit')
            except zipfile.BadZipFile: raise HTTPException(400,'Excel workbook is not a valid XLSX archive')
            df=pd.read_excel(BytesIO(content),dtype=str,keep_default_na=False)
        else: raise HTTPException(415,'Upload a CSV or XLSX spreadsheet')
    except HTTPException: raise
    except Exception as e: raise HTTPException(400,f'Could not read spreadsheet: {e}')
    if len(df)>1000: raise HTTPException(422,'A batch may contain at most 1,000 rows')
    if len(df)<1: raise HTTPException(422,'Spreadsheet has no participant rows')
    df=df.fillna(''); return [str(x) for x in df.columns],df.to_dict(orient='records')

@app.post('/api/participants/validate')
async def validate_participants(file:UploadFile=File(...)):
    headers,rows=await read_spreadsheet(file)
    return {'headers':headers,'rows':rows[:1000],'row_count':len(rows),'suggested_mappings':{k:next((h for h in headers if h.lower().replace(' ','_')==k), '') for k in ['participant_name','event_name','event_date','certificate_type','certificate_id']}}

@app.post('/api/jobs')
async def create_job(template_id:str=Form(...),mappings:str=Form(...),file:UploadFile=File(...),skip_rows:str=Form('[]')):
    t=template(template_id)
    if not t: raise HTTPException(404,'Template not found')
    headers,rows=await read_spreadsheet(file)
    try: mapping=json.loads(mappings); skips={int(n) for n in json.loads(skip_rows)}
    except Exception: raise HTTPException(422,'Mappings or skipped row list is invalid JSON')
    name_field=next((f for f in t['fields'] if f['type']=='participant_name'),None)
    if not name_field or mapping.get(name_field['key']) not in headers: raise HTTPException(422,'Add a participant name field and map a spreadsheet column to it')
    if len(rows)>1000: raise HTTPException(422,'At most 1,000 rows are allowed')
    jid=str(uuid.uuid4()); valid_count=0
    with connect() as c:
        c.execute('INSERT INTO jobs(id,template_id,status,total,created_at,source_rows,mappings) VALUES(?,?,?,?,?,?,?)',(jid,template_id,'queued',len(rows),now(),'[]',json.dumps(mapping)))
        for i,row in enumerate(rows,1):
            data={field['key']:str(row.get(mapping.get(field['key'],''),'')).strip() for field in t['fields'] if mapping.get(field['key'])}
            data['participant_name']=str(row.get(mapping[name_field['key']],'')).strip()
            empty=not any(str(v).strip() for v in row.values()); missing=not data.get('participant_name')
            status='skipped' if i in skips or empty or missing else 'queued'
            error='Organizer skipped row' if i in skips else ('Empty row' if empty else ('Missing participant name' if missing else None))
            rid=str(uuid.uuid4()); c.execute('INSERT INTO records(id,job_id,row_num,data,status,error) VALUES(?,?,?,?,?,?)',(rid,jid,i,json.dumps(data,ensure_ascii=False),status,error))
            if status=='queued': valid_count+=1
        c.execute('UPDATE jobs SET skipped=? WHERE id=?',(len(rows)-valid_count,jid)); c.commit()
    threading.Thread(target=process_job,args=(jid,),daemon=True).start()
    return {'id':jid,'status':'queued','total':len(rows),'queued':valid_count}

@app.get('/api/jobs')
def list_jobs():
    with connect() as c: rows=c.execute('SELECT id,template_id,status,total,successful,failed,skipped,created_at,completed_at FROM jobs ORDER BY created_at DESC').fetchall()
    return [dict(x) for x in rows]
@app.get('/api/jobs/{jid}')
def get_job(jid):
    j=job(jid)
    if not j: raise HTTPException(404,'Job not found')
    j.pop('source_rows',None); j.pop('mappings',None)
    return j
@app.get('/api/jobs/{jid}/download')
def download_job(jid):
    j=job(jid,False)
    if not j: raise HTTPException(404,'Job not found')
    p=OUTPUTS/jid/'certificates.zip'
    if j['status'] not in ('completed','completed_with_errors'): raise HTTPException(409,'ZIP is not ready yet')
    if j['successful']==0: raise HTTPException(409,'This job has no successful certificates')
    if not p.is_file(): raise HTTPException(409,'ZIP is not ready yet')
    return FileResponse(p,media_type='application/zip',filename=f'certifyflow-{jid[:8]}.zip')
@app.get('/api/jobs/{jid}/report')
def report(jid):
    if not job(jid,False): raise HTTPException(404,'Job not found')
    p=OUTPUTS/jid/'report.csv'
    if not p.is_file(): raise HTTPException(409,'Report is not ready yet')
    return FileResponse(p,media_type='text/csv',filename=f'certifyflow-report-{jid[:8]}.csv')
@app.get('/api/jobs/{jid}/certificates/{rid}')
def certificate(jid,rid):
    with connect() as c: r=c.execute("SELECT output_file FROM records WHERE id=? AND job_id=? AND status='success'",(rid,jid)).fetchone()
    if not r: raise HTTPException(404,'Certificate not found')
    p=OUTPUTS/jid/r['output_file']
    if not p.is_file(): raise HTTPException(404,'Certificate file not found')
    return FileResponse(p,media_type='application/pdf',filename=r['output_file'])
@app.post('/api/jobs/{jid}/retry-failed')
def retry(jid):
    j=job(jid,False)
    if not j: raise HTTPException(404,'Job not found')
    if j['status']=='running': raise HTTPException(409,'Job is still running')
    with connect() as c:
        n=c.execute("SELECT count(*) FROM records WHERE job_id=? AND status='failed'",(jid,)).fetchone()[0]
        if not n: raise HTTPException(409,'There are no failed records to retry')
        c.execute('UPDATE jobs SET status=?,failed=0 WHERE id=?',('queued',jid)); c.commit()
    threading.Thread(target=process_job,args=(jid,True),daemon=True).start(); return {'queued_for_retry':n}
@app.delete('/api/jobs/{jid}')
def delete_job(jid):
    with connect() as c:
        r=c.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: raise HTTPException(404,'Job not found')
        if r['status']=='running': raise HTTPException(409,'Cannot delete a running job')
        c.execute('DELETE FROM jobs WHERE id=?',(jid,)); c.commit()
    shutil.rmtree(OUTPUTS/jid,ignore_errors=True); return {'deleted':True}

app.mount('/',StaticFiles(directory=Path(__file__).parent.parent/'web',html=True),name='web')
