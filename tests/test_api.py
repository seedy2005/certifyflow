from fastapi.testclient import TestClient
from app.main import app

def authenticated_client():
    client=TestClient(app)
    session=client.get("/api/auth/session")
    login=client.post("/api/auth/login",json={"username":"test-organizer","password":"test-only-password-value-123456"},headers={"X-CSRF-Token":session.json()["csrf_token"]})
    assert login.status_code==200,login.text
    client.headers.update({"X-CSRF-Token":login.json()["csrf_token"]})
    return client

from PIL import Image
from io import BytesIO
import json,time,zipfile

def test_health_and_unsupported_template_upload():
    with authenticated_client() as client:
        assert client.get('/api/health').json()=={'status':'ok'}
        r=client.post('/api/templates',files={'file':('bad.exe',b'nope','application/octet-stream')})
        assert r.status_code==415

def test_duplicate_names_remain_distinct_rows():
    with authenticated_client() as client:
        r=client.post('/api/participants/validate',files={'file':('people.csv',b'participant_name\nSame Name\nSame Name\n','text/csv')})
        assert r.status_code==200
        assert len(r.json()['rows'])==2
        assert r.json()['rows'][0]['participant_name']==r.json()['rows'][1]['participant_name']

def test_full_batch_persists_outputs_report_and_zip():
    image=BytesIO(); Image.new('RGB',(500,300),'white').save(image,format='PNG')
    fields=[dict(key='participant_name',type='participant_name',x=.5,y=.5,alignment='center',font_family='Helvetica',font_size=24,min_font_size=12,color='#18392F',max_width=.9,required=True)]
    with authenticated_client() as client:
        uploaded=client.post('/api/templates',files={'file':('sample.png',image.getvalue(),'image/png')})
        assert uploaded.status_code==200,uploaded.text
        tid=uploaded.json()['id']
        saved=client.put(f'/api/templates/{tid}/config',json={'name':'Integration template','page_index':0,'fields':fields})
        assert saved.status_code==200,saved.text
        assert client.get(f'/api/templates/{tid}').json()['fields'][0]['key']=='participant_name'
        csv='full_name\nSame Name\nSame Name\nമലയാളം\n\n'.encode('utf-8')
        response=client.post('/api/jobs',data={'template_id':tid,'mappings':json.dumps({'participant_name':'full_name'}),'skip_rows':'[]'},files={'file':('people.csv',csv,'text/csv')})
        assert response.status_code==200,response.text
        jid=response.json()['id']; result=None
        for _ in range(60):
            result=client.get(f'/api/jobs/{jid}').json()
            if result['status'] in ('completed','completed_with_errors'):break
            time.sleep(.1)
        assert result['status']=='completed_with_errors',result
        assert result['successful']==2 and result['failed']==1 and result['skipped']==1
        before={r['id']:r['output_file'] for r in result['records'] if r['status']=='success'}
        archive=client.get(f'/api/jobs/{jid}/download'); assert archive.status_code==200
        with zipfile.ZipFile(BytesIO(archive.content)) as z:
            assert len(z.namelist())==2
            assert all(item.file_size<1_000_000 for item in z.infolist())
        report=client.get(f'/api/jobs/{jid}/report'); assert report.status_code==200
        assert report.text.count('success')==2
        assert 'failed' in report.text and 'cannot render all characters' in report.text
        assert 'skipped,,Empty row' in report.text
        assert client.get(f'/api/jobs/{jid}/certificates/not-a-record').status_code==404
        assert client.post(f'/api/jobs/{jid}/retry-failed').status_code==200
        for _ in range(60):
            result=client.get(f'/api/jobs/{jid}').json()
            if result['status']=='completed_with_errors': break
            time.sleep(.1)
        after={r['id']:r['output_file'] for r in result['records'] if r['status']=='success'}
        assert after==before
        assert next(r for r in result['records'] if r['status']=='failed')['retry_count']==1


def test_api_requires_login_and_csrf():
    with TestClient(app) as client:
        assert client.get('/api/templates').status_code==401
        session=client.get('/api/auth/session').json()
        assert client.post('/api/auth/login',json={'username':'test-organizer','password':'test-only-password-value-123456'}).status_code==403
        login=client.post('/api/auth/login',json={'username':'test-organizer','password':'test-only-password-value-123456'},headers={'X-CSRF-Token':session['csrf_token']})
        assert login.status_code==200
        token=login.json()['csrf_token']
        assert client.get('/api/templates').status_code==200
        assert client.post('/api/auth/logout',headers={'X-CSRF-Token':token}).status_code==200
        assert client.get('/api/templates').status_code==401
