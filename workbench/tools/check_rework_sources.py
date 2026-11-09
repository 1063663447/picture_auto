"""Offline original/result vision, lineage and multi-round rework regression.
No real credentials, paid generation or external HTTP. All artifacts are temporary.
"""
from __future__ import annotations
import asyncio
import base64
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from skillbridge import agent, api
from skillbridge.harness_media import HarnessImage, inspect_images, rework_images
from skillbridge.job import Job, Task
from skillbridge.pricing import Pricer
from skillbridge.service import Workbench
from skillbridge.skill import load_skill
from skillbridge.sources import job_image_path, original_uploads

ROOT=Path(__file__).resolve().parents[1]

class ReworkSourceChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wb-rework-sources-')
        self.root=Path(self.temp.name)
        self.stack=[]
        for target in ['socket.create_connection','httpx.AsyncClient.send']:
            mock=patch(target,side_effect=AssertionError('External network forbidden'))
            mock.start();self.stack.append(mock)
        bench=self.bench=Workbench.__new__(Workbench)
        bench.config={'default_provider':'fixture','server':{'auth_token':'test-source-token','session_secret':'fixture-secret','login_required':True},
                      'defaults':{'rework':{'refiner':'template'},'harness':{'enabled':True,'model':'fixture-chat','api_key_env':'FIXTURE_KEY','base_url':'https://invalid.invalid'}}}
        bench.env={'FIXTURE_KEY':'not-a-real-key'}
        bench.data_dir=self.root;bench.jobs_dir=self.root/'jobs';bench.jobs_dir.mkdir()
        bench.jobs={};bench.skill=load_skill(ROOT.parent/'apparel-style-recolor')
        provider=SimpleNamespace(name='fixture',cfg={'model':'fixture-image'},max_images=4,
                                 setting=lambda key,default=None:default,info=lambda:{'label':'测试接口'})
        bench.get_provider=lambda name=None:provider
        bench.pricer=Pricer(self.root/'unused-pricing.json')
        bench._quota_guard=lambda *args:None
        bench.sync_pending=lambda:0
        async def shutdown():pass
        bench.shutdown=shutdown
        self.origin=self.make_job('20260101-000000-aaaa')
        a=self.make_task(self.origin,'front','item-01','view-01')
        b=self.make_task(self.origin,'back','item-01','view-02')
        other=self.make_task(self.origin,'other','item-02','view-01')
        self.front=a;self.back=b;self.other=other
    def tearDown(self):
        for mock in reversed(self.stack):mock.stop()
        self.temp.cleanup()
    def make_job(self,job_id,**params):
        job=Job(job_id,{'garment_type':'hoodie','garment_name':'Hoodie','provider':'fixture','model':'fixture-image','size':'auto','max_retries':1,**params},root=self.bench.jobs_dir/job_id,status='done')
        job.root.mkdir(parents=True);self.bench.jobs[job.id]=job
        return job
    def make_task(self,job,tid,item,view,*,composite=False):
        source=job.root/'inputs'/item/(tid+'.png');source.parent.mkdir(parents=True,exist_ok=True)
        image=Image.new('RGB',(800,800),'white');draw=ImageDraw.Draw(image)
        draw.rectangle([20,80,350,720],fill='#111111');draw.rectangle([400,40,760,760],fill='#222222')
        draw.text((100,200),'FRONT',fill='red');draw.text((500,300),'BACK',fill='red');image.save(source)
        out=job.root/'staging'/(tid+'.png');out.parent.mkdir(exist_ok=True)
        Image.new('RGB',(1254,1254),'#dddddd').save(out)
        task=Task(tid,item,view,'white','#fff','白色',source=source.relative_to(job.root).as_posix(),out=out.relative_to(job.root).as_posix(),
                  status='done',prompt='old single-view context',subject='garment',garment='hoodie',garment_name='Hoodie')
        job.tasks.append(task)
        return task
    def rework(self,job,tasks):
        return asyncio.run(self.bench.create_rework_job(job.id,[t.id for t in tasks],'恢复原图正反面构图与各自印花，保持成品白色',provider='fixture',subject='garment'))
    def test_same_item_views_are_preserved_without_other_items(self):
        rows=original_uploads(self.origin,self.front,self.bench.get_job)
        self.assertEqual([r.view for r in rows],['view-01','view-02'])
        self.assertTrue(all(r.item=='item-01' for r in rows))
        self.assertEqual(rows[0].path,job_image_path(self.origin,self.front.source))
    def test_legacy_rework_traces_through_multiple_rounds(self):
        one=self.make_job('20260101-000000-bbbb',kind='rework',source_job=self.origin.id,source_tasks=[self.front.id])
        a=self.make_task(one,'legacy','item-01','');a.color='white-v2'
        two=self.make_job('20260101-000000-cccc',kind='rework',source_job=one.id,source_tasks=[a.id])
        b=self.make_task(two,'legacy-twice','item-01','');b.color='white-v2-v2'
        rows=original_uploads(two,b,self.bench.get_job)
        self.assertEqual([r.path.name for r in rows],['front.png','back.png'])
        self.assertTrue(all(r.job_id==self.origin.id for r in rows))
    def test_missing_or_cyclic_ancestry_never_treats_result_as_upload(self):
        broken=self.make_job('20260101-000000-dddd',kind='rework',source_job='missing',source_tasks=['missing'])
        task=self.make_task(broken,'only-result','item-01','');task.color='white-v2'
        self.assertEqual(original_uploads(broken,task,self.bench.get_job),[])
        broken.params.update(source_job=broken.id,source_tasks=[task.id],source_task_map={task.id:task.id})
        self.assertEqual(original_uploads(broken,task,self.bench.get_job),[])
        media,notes=rework_images(broken,[task],self.bench.get_job)
        self.assertEqual([r.role for r in media],['current'])
        self.assertIn('来源无法追溯',''.join(notes))
    def test_job_path_cannot_escape_root(self):
        outside=self.root/'outside.png';Image.new('RGB',(10,10)).save(outside)
        self.assertIsNone(job_image_path(self.origin,'../../outside.png'))
        self.assertIsNone(job_image_path(self.origin,str(outside)))
        self.assertIsNone(job_image_path(self.origin,'missing.png'))
    def test_new_rework_preserves_unique_views_and_local_original_copies(self):
        rework=self.rework(self.origin,[self.front,self.back])
        self.assertEqual(len({t.id for t in rework.tasks}),2)
        self.assertEqual([t.view for t in rework.tasks],['view-01','view-02'])
        self.assertEqual([t.garment for t in rework.tasks],['hoodie','hoodie'])
        self.assertEqual(set(rework.params['source_task_map'].values()),{self.front.id,self.back.id})
        for task,parent in zip(rework.tasks,[self.front,self.back]):
            self.assertEqual((rework.root/task.source).read_bytes(),(self.origin.root/parent.out).read_bytes())
            refs=rework.params['original_sources'][task.id]
            self.assertEqual(len(refs),2)
            self.assertEqual((rework.root/refs[0]).read_bytes(),(self.origin.root/parent.source).read_bytes())
        for task in rework.tasks:
            output=rework.root/'staging'/(task.id+'.png');output.parent.mkdir(exist_ok=True)
            output.write_bytes((rework.root/task.source).read_bytes())
            task.out=output.relative_to(rework.root).as_posix();task.status='done'
        rework.status='done';rework.save()
        self.assertEqual(len(list((rework.root/'originals').iterdir())),2)
        payload=json.loads(rework.path().read_text(encoding='utf-8'))
        loaded=Job.from_payload(payload,rework.root)
        self.bench.jobs[loaded.id]=loaded
        again=self.rework(loaded,loaded.tasks[:1])
        rows=original_uploads(again,again.tasks[0],lambda _: (_ for _ in ()).throw(KeyError('upstream removed')))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0].path.read_bytes(),(self.origin.root/self.front.source).read_bytes())
        self.assertTrue(all(r.job_id==self.origin.id for r in rows))
    def test_batched_media_pairs_label_actual_roles_and_omissions(self):
        tasks=[self.front,self.back,self.other]
        for i in range(3,7):tasks.append(self.make_task(self.origin,'extra'+str(i),'item-'+str(i),'view-01'))
        images,notes=rework_images(self.origin,tasks,self.bench.get_job,limit=6)
        self.assertLessEqual(len(images),6)
        self.assertIn('未附带',''.join(notes))
        self.assertEqual(images[0].role,'original');self.assertEqual(images[1].role,'current')
        self.assertIn('原始上传图',images[0].label);self.assertIn('待改成品',images[1].label)
        only,notes=rework_images(self.origin,[self.other],self.bench.get_job)
        self.assertTrue(all('item-02' in row.label for row in only))
    def test_resolution_warning_is_per_file_not_generic(self):
        high=HarnessImage(self.origin.root/self.front.out,'current','待改成品 item-01')
        text=inspect_images([high])
        self.assertIn('1254×1254',text);self.assertIn('1254px ≥ 1200px',text)
        self.assertNotIn('分辨率偏低',text);self.assertNotIn('低于 1200px；',text)
        low=HarnessImage(self.origin.root/self.front.source,'original','原始上传图 item-01')
        text=inspect_images([low,high])
        self.assertIn('图1 低于 1200px',text)
        self.assertIn('图2 — 待改成品',text)
        self.assertIn('1254px ≥ 1200px',text)
    def test_chat_content_interleaves_labels_with_image_bytes(self):
        images=[agent.encode_image(self.origin.root/self.front.source),agent.encode_image(self.origin.root/self.front.out)]
        content=agent._user_content('compare',images,['原始上传图 item-01','待改成品 item-01'])
        self.assertEqual([row['type'] for row in content],['text','text','image_url','text','image_url'])
        self.assertIn('原始上传图',content[1]['text']);self.assertIn('待改成品',content[3]['text'])
        with Image.open(io.BytesIO(base64.b64decode(content[2]['image_url']['url'].split(',')[1]))) as im:self.assertEqual(im.size,(800,800))
        with Image.open(io.BytesIO(base64.b64decode(content[4]['image_url']['url'].split(',')[1]))) as im:self.assertEqual(max(im.size),1200)
    def test_harness_endpoint_sends_original_and_target_to_agent_and_inspector(self):
        seen={}
        async def fake_agent(*,ctx,**kwargs):
            seen['labels']=ctx.image_labels
            seen['images']=ctx.images
            seen['intro']=agent._intake_intro(ctx,'原图有正反面')
            seen['inspection']=await ctx.inspect()
            self.assertEqual(await ctx.inspect(),seen['inspection'])
            yield {'event':'delta','data':{'text':'原图与成品已按角色附带'}}
            yield {'event':'done','data':{'ok':True}}
        from skillbridge.harness_media import inspect_images as actual_inspect
        with patch.object(api,'Workbench',return_value=self.bench),patch.object(api.agent,'run_agent',fake_agent),patch.object(api,'inspect_images',wraps=actual_inspect) as inspection:
            app=api.create_app('unused')
            with TestClient(app) as client:
                response=client.post('/api/harness/stream',headers={'x-workbench-token':'test-source-token'},json={'mode':'rework','job_id':self.origin.id,'task_ids':[self.front.id],'instruction':'先对比原图与成品的正反面，不要提交','subject':'garment','provider':'fixture'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(inspection.call_count,1)
        self.assertIn('原始上传图',seen['labels'][0]);self.assertIn('待改成品',seen['labels'][1])
        self.assertEqual(len(seen['images']),3)
        self.assertIn('view-02',''.join(seen['labels']))
        self.assertNotIn('other.png',''.join(seen['labels']))
        self.assertIn('一张文件可能含正反面',seen['inspection'])
        self.assertIn('1254px ≥ 1200px',seen['inspection'])
        self.assertIn('文件数不等于服装视角数',seen['intro'])

    def test_failed_first_encoding_keeps_inspection_and_attachment_numbers_aligned(self):
        seen={};encode=agent.encode_image
        def selective_encode(path):
            return None if Path(path)==self.origin.root/self.front.source else encode(path)
        async def fake_agent(*,ctx,**kwargs):
            seen['labels']=ctx.image_labels;seen['count']=len(ctx.images)
            seen['inspection']=await ctx.inspect();seen['notes']=ctx.image_notes
            yield {'event':'done','data':{'ok':True}}
        with patch.object(api,'Workbench',return_value=self.bench),patch.object(api.agent,'run_agent',fake_agent),patch.object(api.agent,'encode_image',side_effect=selective_encode):
            with TestClient(api.create_app('unused')) as client:
                response=client.post('/api/harness/stream',headers={'x-workbench-token':'test-source-token'},json={'mode':'rework','job_id':self.origin.id,'task_ids':[self.front.id],'instruction':'只对照，不生成','subject':'garment','provider':'fixture'})
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(seen['count'],2)
        self.assertIn('待改成品',seen['labels'][0])
        self.assertIn('图1 — 待改成品',seen['inspection'])
        self.assertIn('1254px ≥ 1200px',seen['inspection'])
        self.assertIn('view-02',seen['labels'][1])
        self.assertIn('编码失败',seen['notes'])
        self.assertIn('front.png',seen['notes'])

if __name__=='__main__':unittest.main(verbosity=2)
