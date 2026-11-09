"""Check production Black Glass assets, DOM hooks and absence of sandbox wiring (offline)."""
from __future__ import annotations
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

WEB=Path(__file__).resolve().parents[1]/'web'
class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=[];self.resources=[];self.attributes=[]
    def handle_starttag(self,tag,attrs):
        row=dict(attrs);self.attributes.append((tag,row))
        if row.get('id'):self.ids.append(row['id'])
        if tag in {'script','link','img'}:
            value=row.get('href') if tag=='link' else row.get('src')
            if value:self.resources.append(value)

def main()->None:
    html=(WEB/'index.html').read_text(encoding='utf-8')
    app=(WEB/'app.js').read_text(encoding='utf-8')
    ui=(WEB/'blackglass-ui.js').read_text(encoding='utf-8')
    workflow=(WEB/'ai-workflow.js').read_text(encoding='utf-8')
    page=Page();page.feed(html)
    assert len(page.ids)==len(set(page.ids)), 'Duplicate DOM IDs'
    optional={'patternMode','sizePreview','outputFormat','downloadFmt','concurrency','rpm','garment','colors'}
    dynamic={'jobLoadingSurface'}
    referenced=set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9]+)"\)',app))
    assert not referenced-set(page.ids)-optional-dynamic, f'Missing hooks: {referenced-set(page.ids)-optional-dynamic}'
    for resource in page.resources:
        target=urlsplit(resource)
        assert not target.scheme and not target.netloc, f'External resource: {resource}'
        assert (WEB/target.path.lstrip('/')).is_file(), f'Missing asset: {resource}'
        if resource.endswith(('.js','.css')) or '?v=' in resource:
            assert 'v=9.4.1' in resource, f'Unversioned script/style: {resource}'
    assert all(word not in html+app+ui for word in ['FlowDemo','mock.js','catalog.js','demoScenario','demoReset','tone-01','prism-syntax-check'])
    assert 'window.fetch=' not in app+ui, 'Production must not replace fetch'
    assert 'thumb_small' in app and 'preview' in app, 'Thumbnail / preview mapping missing'
    assert '/api/jobs/' in app and 'export${query}' in app, 'Backend downloads must remain in use'
    assert '/api/usage' in app and 'person_alert' in ui and 'master_task' in ui, 'Business metadata missing'
    assert 'pollEpoch' in app and 'controller.signal' in app and 'state.jobLoading' in app, 'Task switch/poll guards missing'
    assert 'submissionBlockedReason()' in app and 'reworkModelRequest' in app and 'modelManual.${providerName}' in app, 'Submission/model guards missing'
    assert 'DoubleSide' in (WEB/'blackglass/atmosphere.js').read_text(encoding='utf-8'), 'Particle face culling regression'
    assert 'data-finish="graphite"' in html and '/graphite.css' in html, 'Selected graphite finish missing'
    assert 'id="openHarnessBtn"' not in html and 'id="railChat"' not in html and '$("openHarnessBtn")' not in app, 'Redundant communication entry remains'
    assert 'ai-dialog' in html and 'aiPhaseLabel' in html and 'aiResumeBtn' in html, 'AI modal/status hooks missing'
    assert 'window.AIWorkflow' in workflow and 'AIWorkflow?.stream' in app and 'harnessStreamDone' in app, 'Stream feedback wiring missing'
    assert all(text not in html for text in ['GRAPHITE / SILVER VIOLET','石墨银紫创作工作台','按工作台设置计价']), 'Removed display labels remain'
    assert '[hidden]' in (WEB/'style.css').read_text(encoding='utf-8')
    print(f'PASS: {len(page.ids)} unique IDs, {len(page.resources)} local resources, production routes, particle material and no sandbox wiring')

if __name__=='__main__':main()
