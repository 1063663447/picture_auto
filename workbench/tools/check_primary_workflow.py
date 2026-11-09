"""Offline regression for primary pricing, frozen job timing and harness forwarding.
Uses temporary files, real pricing/job/service methods and an in-process API.
No provider generation, real credentials, Feishu or external HTTP is used.
"""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image
from skillbridge import api, agent
from skillbridge.job import Job
from skillbridge.pricing import Pricer, UsageLedger
from skillbridge.service import Workbench, WorkbenchError

class WorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wb-policy-')
        self.root=Path(self.temp.name)
        price=self.root/'pricing.json'
        price.write_text(json.dumps({'currency':'CNY','models':{'image-a':9.9,'image-b':{'tiers':{'1K':0.4,'4K':4.0}}}}),encoding='utf-8')
        self.pricer=Pricer(price,provider_prices={'a6api':{'image-a':0.001}},provider_fixed_prices={'a6api':0.038})
    def tearDown(self):
        self.temp.cleanup()
    def test_config_primary_policy(self):
        cfg=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['default_provider'],'a6api')
        self.assertEqual(cfg['providers']['a6api']['label'],'主接口')
        self.assertEqual(cfg['providers']['terra']['label'],'备用接口')
        self.assertEqual(cfg['providers']['a6api']['fixed_unit_price'],0.038)
    def test_fixed_price_all_models_sizes_and_ledger(self):
        for model in ['image-a','image-b','unknown-image']:
            for size in ['auto','1024x1024','3072x4096']:
                self.assertEqual(self.pricer.price(model,size,'a6api').unit,0.038)
        self.pricer.raw['image-a']=999
        self.assertEqual(self.pricer.estimate('image-a',3,'4096x4096','a6api')['total'],0.114)
        self.assertEqual(self.pricer.price('image-a','auto','terra').unit,999)
        ledger=UsageLedger(self.root,self.pricer)
        row=ledger.record(job_id='one',model='unknown-image',images=3,provider='a6api',account='member')
        self.assertEqual(row['unit'],0.038)
        self.assertEqual(row['cost'],0.114)
        ledger.record(job_id='one',model='image-a',images=9,provider='a6api')
        self.assertEqual(len(ledger.entries),1)
    def make_service(self):
        bench=Workbench.__new__(Workbench)
        configs={'terra':{'label':'备用接口','show_in_ui':True},'hidden':{'label':'hidden','show_in_ui':False},'a6api':{'label':'主接口','show_in_ui':True,'fixed_unit_price':0.038}}
        bench.config={'default_provider':'a6api','defaults':{'size':'auto'}}
        bench.providers={name:SimpleNamespace(name=name,cfg=cfg,info=lambda name=name,cfg=cfg:{'name':name,'label':cfg['label'],'ready':True}) for name,cfg in configs.items()}
        bench.data_dir=self.root
        bench.pricer=self.pricer
        bench._model_cache={}
        return bench
    def test_order_and_cached_model_repricing(self):
        bench=self.make_service()
        rows=bench.provider_payload()
        self.assertEqual([r['label'] for r in rows],['主接口','备用接口'])
        self.assertTrue(rows[0]['default'])
        self.assertEqual(rows[0]['fixed_unit_price'],0.038)
        old={'provider':'a6api','models':[{'id':'image-a','image':True,'price':0.001,'price_label':'old'}]}
        bench._model_cache={'a6api':(time.time(),old)}
        result=asyncio.run(bench.list_models('a6api'))
        self.assertEqual(result['models'][0]['price'],0.038)
        self.assertEqual(old['models'][0]['price'],0.001)
        (self.root/'models-a6api.json').write_text(json.dumps(old),encoding='utf-8')
        result=bench._offline_models('a6api','offline fixture')
        self.assertEqual(result['models'][0]['price_label'],'¥0.038/张')
    def test_quota_uses_selected_provider(self):
        bench=self.make_service()
        bench.accounts=SimpleNamespace(find=lambda name:SimpleNamespace(username=name,display='Fixture member',is_admin=False,quota=0.075))
        bench._feishu_account_usage=lambda:{}
        bench.usage=UsageLedger(self.root,self.pricer)
        with self.assertRaises(WorkbenchError):
            bench._quota_guard('member','unknown-image',2,'auto','a6api')
    def test_total_duration_freezes_and_survives_reload(self):
        job=Job('duration',{},created_at=100,root=self.root)
        job.status='done'
        with patch('skillbridge.job.time.time',return_value=142.25):job.save()
        self.assertEqual(job.duration,42.25)
        with patch('skillbridge.job.time.time',return_value=900):job.save()
        self.assertEqual(job.finished_at,142.25)
        restored=Job.from_payload(json.loads(job.path().read_text(encoding='utf-8')),self.root)
        self.assertEqual(restored.duration,42.25)
        legacy=Job.from_payload({'id':'legacy','status':'partial','created_at':100,'updated_at':130},self.root)
        self.assertEqual(legacy.duration,30)
        job.status='running'
        with patch('skillbridge.job.time.time',return_value=950):job.save()
        self.assertIsNone(job.duration)
        self.assertIsNone(job.finished_at)
    def fixture_bench(self):
        image=self.root/'source.png';Image.new('RGB',(64,80),(210,160,130)).save(image)
        reference=SimpleNamespace(name_zh='白色',label='white',hex='#FFFFFF',file_code='WH[0]',code='WH[0]',rgb=(255,255,255),effect=None)
        garment=SimpleNamespace(slug='fixture',display_name='Fixture',colors=[reference],color=lambda _:reference,resolve_colors=lambda keys:[reference],name_en='FIXTURE',cuff='plain',sleeve='short',fit='')
        bench=SimpleNamespace(config={'server':{'auth_token':'fixture-only-token','login_required':True,'session_secret':'fixture-secret'}},env={'FIXTURE_AGENT_KEY':'not-a-real-key'})
        bench.defaults=lambda:{'harness':{'enabled':True,'api_key_env':'FIXTURE_AGENT_KEY','model':'fixture-agent','base_url':'https://invalid.invalid'}}
        bench.pricer=self.pricer
        bench.skill=SimpleNamespace(types={'fixture':garment},type_by_alias=lambda _:garment)
        bench.get_provider=lambda name=None:SimpleNamespace(name=name or 'a6api',cfg={'model':'image-a'})
        bench.draft_image_paths=lambda _: [image]
        bench.draft_uploads=lambda _:[('item-01_view-01.png',image.read_bytes())]
        bench.job_dir=lambda _:self.root
        # 9.4.0 起 harness_stream 会逐任务读 source/garment/color/color_hex/item/view
        # （构建 batch_context 与 target_prompts）。这个 fixture 是 9.1.0 写的，缺字段会直接
        # AttributeError，让本脚本变成「假红」——补齐成真实 Task 的字段集。
        source_task=SimpleNamespace(id='task-1',out='source.png',prompt='previous prompt',
                                    color_name='白色',color='white',color_hex='#FFFFFF',
                                    garment='fixture',garment_name='Fixture',item='item-01',view='',
                                    source='item-01/item-01.png',subject='person')
        source=SimpleNamespace(id='fixture-source-job',root=self.root,
                               params={'garment_type':'fixture','model':'image-a'},
                               tasks=[source_task])
        bench.get_job=lambda _:source
        bench.created=[];bench.started=[];bench.model_requests=[]
        def create(uploads,**kwargs):
            bench.created.append(kwargs)
            return SimpleNamespace(id='fixture-job',params={'concurrency':'auto','estimate':self.pricer.estimate(kwargs['model'],1,kwargs.get('size'),'a6api')},tasks=[object()])
        async def rework(source,targets,text,**kwargs):return create([],**kwargs)
        async def models(name):
            bench.model_requests.append(name)
            return {'models':[{'id':'image-a','image':True,'price_label':self.pricer.price('image-a','auto',name).label()}]}
        async def shutdown():pass
        bench.create_job=create;bench.create_rework_job=rework;bench.start=bench.started.append
        bench.list_models=models;bench.sync_pending=lambda:0;bench.shutdown=shutdown
        return bench
    def exercise_harness(self,subject='garment',mode='intake',fail_after=False):
        bench=self.fixture_bench();seen={}
        async def fake_agent(*,ctx,**kwargs):
            seen['subject']=ctx.subject
            seen['models']=await ctx.list_models()
            seen['estimate']=await ctx.estimate('',1)
            submit=ctx.submit_rework if mode=='rework' else ctx.submit_generation
            await submit('已对齐并优化的执行要求')
            await submit('重复工具调用不得重复创建')
            if fail_after:raise RuntimeError('controlled post-submit interruption')
            yield {'event':'prompt','data':{'prompt':'optimized','requirements':'已对齐并优化的执行要求'}}
            yield {'event':'done','data':{'ok':True}}
        body={'mode':mode,'draft_id':'fixture-draft','garment_type':'fixture','colors':['white'],'model':'image-a','provider':'a6api','instruction':'请先对齐需求再生成','subject':subject,'size':'2048x2048','quality':'medium','output_format':'png','count':1,'shape_lock':'master','send_reference':True}
        if mode=='rework':body.update(job_id='source-job',task_ids=['task-1'])
        with patch.object(api,'Workbench',return_value=bench),patch.object(api.agent,'run_agent',fake_agent),patch.object(api.prompts,'build_single_pass',return_value='fixture base prompt'),patch('httpx.AsyncClient.send',side_effect=AssertionError('External HTTP forbidden')):
            app=api.create_app('unused')
            with TestClient(app) as client:
                response=client.post('/api/harness/stream',json=body,headers={'x-workbench-token':'fixture-only-token'})
                self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(bench.created),1)
        self.assertEqual(bench.started,['fixture-job'])
        self.assertEqual(bench.created[0]['subject'],subject)
        self.assertEqual(bench.created[0]['provider'],'a6api')
        self.assertEqual(bench.created[0]['size'],'2048x2048')
        self.assertEqual(bench.created[0]['quality'],'medium')
        self.assertEqual(bench.created[0]['account'],'token')
        self.assertEqual(bench.model_requests,['a6api'])
        self.assertIn('0.038',seen['estimate'])
        self.assertIn('0.038',seen['models'])
        self.assertIn('fixture-job',response.text)
        if subject in {'garment','person'}:self.assertEqual(seen['subject'],subject)
        return response
    def test_harness_keeps_explicit_subject_and_one_submission(self):
        for subject in ['garment','person','auto']:
            with self.subTest(subject=subject):self.exercise_harness(subject)
    def test_rework_harness_keeps_subject_and_settings(self):
        self.exercise_harness('garment','rework')
    def test_post_submission_failure_does_not_restart_or_lose_job(self):
        response=self.exercise_harness(fail_after=True)
        self.assertIn('event: job',response.text)
        self.assertIn('勿重复提交',response.text)
    def test_agent_intro_exposes_subject_selection(self):
        text=agent._intake_intro(agent.AgentContext(subject='garment'), '保持印花')
        self.assertIn('garment',text)
        self.assertIn('人物',text)

if __name__=='__main__':unittest.main(verbosity=2)