"""Exercise a built package with no host Python PATH and separate test-only data.
Includes offline recovery, domestic HTTP recovery, singleton, auth, mock job and ZIP.
No paid generation or Feishu notifications; -TestMode disables all external sync.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import zipfile


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--network-repair',action='store_true')
    args=parser.parse_args()
    original=args.package.resolve();scratch=args.workspace.resolve()
    version=json.loads((original/'package-manifest.json').read_text(encoding='utf-8'))['version']
    target=scratch/'同事解压 测试'/f'PictureWorkbench-{version}'
    assert not target.exists(),'Use a fresh regression folder; do not delete user data'
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(original,target,ignore=shutil.ignore_patterns('__pycache__','logs','data','data-portable-test'))
    report={'platform':'Windows x64','host_python_path_removed':True,'checks':[],'paid_calls':0,'external_messages':0}
    logs=scratch/'portable-regression-logs';logs.mkdir(exist_ok=True)
    env=os.environ.copy();windows=Path(env.get('SystemRoot',r'C:\Windows'))
    env['PATH']=str(windows/'System32')+';'+str(windows/'System32/WindowsPowerShell/v1.0')
    for key in ['PYTHONHOME','PYTHONPATH','WORKBENCH_DATA_DIR','WORKBENCH_PORT','WORKBENCH_HOST','PORT']:
        env.pop(key,None)
    env['PYTHONIOENCODING']='utf-8';env['PYTHONUTF8']='1'
    shell=windows/'System32/WindowsPowerShell/v1.0/powershell.exe'
    bootstrap=target/'bootstrap.ps1'
    def run(name,*flags,timeout=180):
        start=time.perf_counter()
        result=subprocess.run([str(shell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(bootstrap),*flags],cwd=target,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
        text=result.stdout.decode('utf-8',errors='replace')
        (logs/(name+'.log')).write_text(text,encoding='utf-8')
        if result.returncode:
            print(text[-3500:],flush=True)
            raise AssertionError(f'{name}: exit {result.returncode}')
        report['checks'].append({'name':name,'exit':result.returncode,'seconds':round(time.perf_counter()-start,2)})
        print('PASS',name,report['checks'][-1]['seconds'],'seconds',flush=True)
        return text
    sentinel=target/'workbench/data/jobs/preflight-active/job.json'
    sentinel.parent.mkdir(parents=True,exist_ok=True)
    sentinel.write_text(json.dumps({'id':'preflight-active','status':'running','params':{},'tasks':[{'status':'running'}]}),encoding='utf-8')
    sentinel_before=sentinel.read_bytes()
    run('fresh-offline-check','-CheckOnly','-OfflineOnly')
    assert sentinel.read_bytes()==sentinel_before,'Preflight modified running task state'
    guard_code='''import runpy\nfrom unittest.mock import patch\nm=runpy.run_path('workbench/portable/launch.py',run_name='portable_guard')\nwith patch('socket.socket.connect',side_effect=AssertionError('External connection forbidden')),patch('socket.create_connection',side_effect=AssertionError('External connection forbidden')),patch('httpx.Client.send',side_effect=AssertionError('HTTP forbidden')),patch('skillbridge.service.Workbench',side_effect=AssertionError('Preflight must not load jobs')):\n    r=m['environment_report'](True)\n    assert r['ok'] and r['types']==13\nprint('Pure preflight: no Workbench state load or network call')\n'''
    guard=subprocess.run([str(target/'runtime/python.exe'),'-B','-c',guard_code],cwd=target,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
    assert guard.returncode==0,guard.stdout.decode('utf-8',errors='replace')
    report['checks'].append({'name':'preflight-no-network-no-job-state-read-or-rewrite','passed':True})
    # Simulate an unpacked/missing runtime and missing Three.js. Cache must suffice.
    shutil.rmtree(target/'runtime')
    (target/'workbench/web/blackglass/vendor/three-0.160.1.min.js').unlink()
    run('no-python-no-three-offline-repair','-CheckOnly','-OfflineOnly',timeout=240)
    assert (target/'runtime/python.exe').is_file()
    assert (target/'workbench/web/blackglass/vendor/three-0.160.1.min.js').is_file()
    if args.network_repair:
        # Trigger a real domestic-source download of the pinned runtime + one small wheel.
        (target/'runtime/python.exe').unlink()
        (target/'offline-cache/python-3.12.10-embed-amd64.zip').unlink()
        (target/'runtime/Lib/site-packages/h11/__init__.py').unlink()
        (target/'offline-cache/wheels/h11-0.16.0-py3-none-any.whl').unlink()
        text=run('domestic-runtime-and-wheel-download-repair','-CheckOnly',timeout=300)
        assert '下载进度' in text and 'h11-0.16.0' in text
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    server_log=(logs/'server.log').open('wb')
    process=subprocess.Popen([str(shell),'-NoProfile','-ExecutionPolicy','Bypass','-File',str(bootstrap),'-NoBrowser','-OfflineOnly','-TestMode','-Port',str(port)],cwd=target,env=env,stdout=server_log,stderr=subprocess.STDOUT)
    server_pid=None
    try:
        import httpx
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base,timeout=20,trust_env=False) as client:
            for _ in range(180):
                try:
                    res=client.get('/api/health',timeout=1)
                    if res.status_code==200:break
                except httpx.HTTPError:pass
                if process.poll() is not None:raise AssertionError('Portable server exited before startup; see server.log')
                time.sleep(.25)
            health=client.get('/api/health').raise_for_status().json()
            assert health['ok'] and health['types']==13
            state=json.loads((target/'workbench/data-portable-test/portable-state.json').read_text(encoding='utf-8'))
            server_pid=state['pid']
            assert state['port']==port
            access=json.loads((target/'workbench/data-portable-test/portable-access.json').read_text(encoding='utf-8'))
            client.get('/',params={'token':access['auth_token']}).raise_for_status()
            session=client.get('/api/session').raise_for_status().json()
            assert session['authenticated'] and session['admin']
            home=client.get('/').raise_for_status().text
            assert f'v={version}' in home
            assert all(label not in home for label in ['GRAPHITE / SILVER VIOLET','石墨银紫创作工作台','按工作台设置计价'])
            assert client.get('/blackglass/vendor/three-0.160.1.min.js').status_code==200
            assert client.get('/api/skill').json()['types']
            report['checks'].append({'name':'portable-server-health-autoauth-static-resources','port':port,'passed':True})
            active_path=target/'workbench/data-portable-test/jobs/duplicate-active/job.json'
            active_path.parent.mkdir(parents=True,exist_ok=True)
            active_path.write_text(json.dumps({'id':'duplicate-active','status':'running','params':{},'tasks':[{'status':'running'}]}),encoding='utf-8')
            active_bytes=active_path.read_bytes()
            run('duplicate-launch-reuses-existing-server','-NoBrowser','-TestMode','-OfflineOnly','-Port',str(port))
            state_after=json.loads((target/'workbench/data-portable-test/portable-state.json').read_text(encoding='utf-8'))
            assert state_after['pid']==server_pid
            assert active_path.read_bytes()==active_bytes,'Duplicate launch altered active task state'
            proxy_env=env.copy();proxy_env.update(HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',NO_PROXY='',no_proxy='')
            proxy_code=f"import runpy; m=runpy.run_path('workbench/portable/launch.py',run_name='portable_proxy'); assert m['read_health']({port})['ok']; print('Local health bypasses proxy')"
            proxy=subprocess.run([str(target/'runtime/python.exe'),'-B','-c',proxy_code],cwd=target,env=proxy_env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
            assert proxy.returncode==0,proxy.stdout.decode('utf-8',errors='replace')
            report['checks'].append({'name':'duplicate-keeps-active-state-and-loopback-bypasses-proxy','passed':True})
            source=target/'workbench/library/refs/23-sorona-tee/white.jpg'
            job=client.post('/api/jobs',data={'provider':'mock','model':'mock','garment_type':'23-sorona-tee','colors':'white','subject':'garment','size':'1024x1536','quality':'high','start':'true'},files={'files':('item-01_view-01.jpg',source.read_bytes(),'image/jpeg')}).raise_for_status().json()['job']
            for _ in range(80):
                job=client.get('/api/jobs/'+job['id']).raise_for_status().json()['job']
                if job['status'] in ['done','partial','failed','canceled']:break
                time.sleep(.15)
            assert job['status']=='done' and job['stats']['done']==1 and job['duration']>=0
            blob=client.get(job['zip']).raise_for_status().content
            assert blob.startswith(b'PK')
            report['checks'].append({'name':'portable-mock-upload-generate-duration-zip','passed':True,'duration':job['duration'],'zip_bytes':len(blob)})
            assert job['params']['provider']=='mock'
    finally:
        if server_pid:
            try:os.kill(server_pid,signal.SIGTERM)
            except OSError:pass
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        server_log.close()
    manifest=json.loads((target/'package-manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(target/'offline-cache/application.zip') as repair_archive:
        assert not any(any(part in {'data','data-portable-test','__pycache__'} for part in Path(name).parts) for name in repair_archive.namelist())
    assert not any(any(part in {'data','data-portable-test','__pycache__'} for part in Path(name).parts) for name in manifest['application_files'])
    allowed={'mirrors.aliyun.com','repo.huaweicloud.com','mirrors.huaweicloud.com','pypi.tuna.tsinghua.edu.cn'}
    from urllib.parse import urlsplit
    assert all(urlsplit(url).scheme=='https' and urlsplit(url).hostname in allowed for artifact in [manifest['python_archive'],*manifest['wheels']] for url in artifact['urls'])
    for base_name,map_name in [(target,'application_files'),(target/'runtime','python_files'),(target/'runtime','dependency_files')]:
        for relative,digest in manifest[map_name].items():
            assert hashlib.sha256((base_name/relative).read_bytes()).hexdigest()==digest,(map_name,relative)
    report['checks'].append({'name':'all-immutable-files-hash-match-and-domestic-HTTPS-only','passed':True})
    report['test_directory']=str(target)
    report['passed']=True
    (scratch/'portable-regression-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'checks':len(report['checks']),'paid_calls':0,'external_messages':0,'report':str(scratch/'portable-regression-report.json')},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
