"""Build the authorized internal Windows-x64 distribution with a private API file.

Never prints the private configuration. Assets must be acquired from domestic
mirrors first; every binary/wheel and installed immutable file is hash-manifested.
Build/intermediate files stay in the caller-supplied staging directory.
"""
from __future__ import annotations
import argparse
from email.parser import BytesParser
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WB = ROOT / 'workbench'
VERSION = '9.6.12'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_wheel(path: Path, target: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            parts = PurePosixPath(entry.filename).parts
            if parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in {'purelib', 'platlib'}:
                    continue
                parts = parts[2:]
            destination = target.joinpath(*parts)
            assert destination.resolve().is_relative_to(target.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.items = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            value = dict(attrs).get('href')
            if value:self.items.append(value)


def wheel_sources(wheel: Path, cache: dict) -> list[str]:
    if wheel.name in cache:
        return cache[wheel.name]
    import httpx
    name = wheel.name.split('-')[0].lower().replace('_', '-')
    sources = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for index in ['https://mirrors.aliyun.com/pypi/simple/', 'https://repo.huaweicloud.com/repository/pypi/simple/']:
            try:
                response = client.get(index + name + '/');response.raise_for_status()
                page = Links();page.feed(response.text)
                for href in page.items:
                    url = urllib.parse.urljoin(str(response.url), href)
                    parsed = urllib.parse.urlsplit(url)
                    if urllib.parse.unquote(PurePosixPath(parsed.path).name) == wheel.name:
                        assert parsed.scheme == 'https'
                        assert parsed.hostname in {'mirrors.aliyun.com', 'repo.huaweicloud.com', 'mirrors.huaweicloud.com'}
                        sources.append(urllib.parse.urlunsplit(parsed._replace(fragment='')))
                        break
            except (httpx.HTTPError, AssertionError):
                continue
    assert sources, 'No verified domestic mirror URL for wheel ' + wheel.name
    cache[wheel.name] = sources
    return sources


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True);shutil.copyfile(source, target)


def add_tree(source: Path, target: Path, suffixes: set[str] | None = None) -> None:
    for path in sorted(source.rglob('*')):
        if not path.is_file() or any(part in {'__pycache__', '.git', '.venv', 'node_modules'} for part in path.parts):
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        copy_file(path, target / path.relative_to(source))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--staging', type=Path, required=True)
    parser.add_argument('--zip', type=Path)
    args = parser.parse_args()
    assets = args.assets.resolve(); stage = args.staging.resolve()
    assert stage != ROOT and ROOT not in stage.parents or stage.is_relative_to(ROOT / 'dist'), 'Staging must be scratch or dist'
    stage.mkdir(parents=True, exist_ok=True)
    assert not (stage / 'workbench/data/portable-state.json').exists(), 'Do not rebuild a running/reused package'
    specs = json.loads((assets / 'assets.json').read_text(encoding='utf-8'))
    mirror_cache_path = assets / 'mirror-urls.json'
    mirror_cache = json.loads(mirror_cache_path.read_text(encoding='utf-8')) if mirror_cache_path.exists() else {}
    print('[1/6] Copying runtime application, references and authorized internal API configuration', flush=True)
    add_tree(WB / 'web', stage / 'workbench/web')
    add_tree(WB / 'skillbridge', stage / 'workbench/skillbridge', {'.py'})
    add_tree(WB / 'library', stage / 'workbench/library', {'.jpg', '.jpeg', '.png', '.webp', '.json'})
    add_tree(ROOT / 'apparel-style-recolor', stage / 'apparel-style-recolor')
    for name in ['serve.py', 'cli.py', 'requirements.txt', 'pricing.json',
                 'CHANGELOG_PORTABLE_9.3.0.md', 'CHANGELOG_WORKFLOW_9.4.0.md', 'CHANGELOG_ARTWORK_9.4.1.md',
                 'CHANGELOG_MODELS_9.4.2.md', 'CHANGELOG_GARMENT_LOCK_9.4.3.md', 'CHANGELOG_GROK_QUALITY_9.4.4.md', 'CHANGELOG_CONVERT_9.4.5.md', 'CHANGELOG_HOOD_CORDS_9.4.6.md', 'CHANGELOG_HOOD_CORDS_9.4.7.md', 'CHANGELOG_SWATCH_9.4.8.md', 'CHANGELOG_STYLE_PROMPTS_9.4.9.md', 'CHANGELOG_STYLES_9.5.0.md', 'CHANGELOG_MASTER_GATE_9.6.0.md', 'CHANGELOG_STYLES_24_9.6.1.md', 'CHANGELOG_CHAT_FIRST_9.6.2.md', 'CHANGELOG_DISTILL_9.6.3.md', 'CHANGELOG_MASTER_WAIT_9.6.4.md', 'CHANGELOG_WHITE_MASTER_9.6.5.md', 'CHANGELOG_ANNOTATE_9.6.6.md', 'CHANGELOG_REVIEW_965_9.6.7.md', 'CHANGELOG_CHAIN_9.6.9.md', 'CHANGELOG_REVIEW_TIGHT_9.6.10.md', 'CHANGELOG_KPI_TOP_9.6.11.md', 'CHANGELOG_FILTER_TOP_9.6.12.md',
                 'HANDOVER.md', 'REVIEW_9.4.1.md']:
        copy_file(WB / name, stage / 'workbench' / name)
    machine_keys={'WORKBENCH_DATA_DIR','WORKBENCH_HOST','WORKBENCH_PORT','WORKBENCH_SESSION_SECRET','WORKBENCH_AUTH_TOKEN','PORT'}
    private_lines=[line for line in (WB/'.env').read_text(encoding='utf-8-sig').splitlines() if line.partition('=')[0].strip() not in machine_keys]
    (stage/'workbench/.env').write_text('\n'.join(private_lines)+'\n',encoding='utf-8')
    for name in ['launch.py', 'requirements-bootstrap.txt']:
        copy_file(WB / 'portable' / name, stage / 'workbench/portable' / name)
    cfg = json.loads((WB / 'config.json').read_text(encoding='utf-8'))
    cfg.update(skill_root='../apparel-style-recolor', data_dir='data', pricing_file='pricing.json')
    cfg.setdefault('library', {})['dir'] = 'library'
    # Local per-machine access secrets are generated automatically, not copied.
    cfg.setdefault('server', {}).update(host='127.0.0.1', port=8787, auth_token='', session_secret='', login_required=True)
    (stage / 'workbench/config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    for name in ['bootstrap.ps1', 'start.bat']:
        copy_file(WB / 'portable' / name, stage / name)
    copy_file(WB / 'portable/start.bat', stage / '一键启动.bat')
    repair_bat = (WB / 'portable/start.bat').read_text(encoding='ascii').replace('bootstrap.ps1\" %*', 'bootstrap.ps1\" -Repair -CheckOnly %*')
    (stage / '一键检查修复.bat').write_bytes(repair_bat.replace('\r\n', '\n').replace('\n', '\r\n').encode('ascii'))
    (stage / '使用说明.txt').write_text(f'生图工作台 {VERSION} · 内部便携版\n\n完整解压到可写目录，双击“一键启动.bat”。自动检查并启动、打开浏览器，无需安装Python、Node或Three.js，无需填写API。适用Windows 10/11 x64 Intel/AMD。\n保持运行窗口打开；关闭窗口停止服务。重复启动会打开已运行工作台。\n文件缺失先从包内offline-cache修复，缓存缺失才从华为云/阿里云国内HTTPS源下载，显示进度并校验SHA256。图片API和AI仍需网络。\n各电脑记录位于workbench/data，不带原电脑历史任务。升级前先停止旧程序、备份旧目录，完整解压新版后可将旧workbench/data复制到对应位置；不要覆盖正在运行的目录。\n9.6.12 本版要点：基准图直接点图框选——在图上拖框标出要改的地方、每个框写一句要求，点「交给 AI 改」就自动发给 AI 并回到审核条，卡片显示「AI 分析中 / 图片生成中」，出图后变「第 N 轮 · 待确认」，可以接着改（第 3、4…轮，不用先采纳），也可以「复原」回第 1 轮并丢掉那一轮的审核记忆；多张基准图可以同时改（互不排队）；审核条与统计（筛选/花费）都挪到顶栏，成品区高度全给图片。详见 workbench 目录下的 CHANGELOG_ANNOTATE_9.6.6.md / CHANGELOG_REVIEW_TIGHT_9.6.10.md / CHANGELOG_KPI_TOP_9.6.11.md。\n包内已包含当前内部主备API、AI与飞书配置，仅供团队内部流转。日志在logs目录。\n', encoding='utf-8-sig')
    cache = stage / 'offline-cache';cache.mkdir(exist_ok=True)
    print('[2/6] Installing private embedded Python and bundled VC runtime DLLs', flush=True)
    runtime = stage / 'runtime';runtime.mkdir(exist_ok=True)
    py = specs['python']; python_archive = assets / py['filename'];assert sha(python_archive) == py['sha256']
    with zipfile.ZipFile(python_archive) as archive:archive.extractall(runtime)
    (runtime / 'python312._pth').write_bytes(b'python312.zip\r\n.\r\nLib\\site-packages\r\n..\\workbench\r\nimport site\r\n')
    python_files = {path.relative_to(runtime).as_posix(): sha(path) for path in sorted(runtime.iterdir()) if path.is_file()}
    copy_file(python_archive, cache / py['filename'])
    print('[3/6] Installing pinned domestic-source wheels with native DLLs', flush=True)
    site = runtime / 'Lib/site-packages';site.mkdir(parents=True, exist_ok=True)
    wheels = []; versions = []
    for i, spec in enumerate(specs['wheels'], 1):
        wheel = assets / 'wheels' / spec['filename'];assert sha(wheel) == spec['sha256']
        extract_wheel(wheel, site)
        copy_file(wheel, cache / 'wheels' / wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            metadata = BytesParser().parsebytes(archive.read(next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))))
            versions.append(f"{metadata['Name']}=={metadata['Version']}")
        urls = wheel_sources(wheel, mirror_cache)
        mirror_cache_path.write_text(json.dumps(mirror_cache, indent=2), encoding='utf-8')
        wheels.append({**spec, 'filename': 'wheels/' + wheel.name, 'urls': urls})
        print(f'  dependency {i}/{len(specs["wheels"])}: {wheel.name}', flush=True)
    dependency_files = {path.relative_to(runtime).as_posix(): sha(path) for path in sorted(site.rglob('*')) if path.is_file()}
    (cache / 'requirements.lock').write_text('\n'.join(sorted(versions))+'\n', encoding='utf-8')
    print('[4/6] Creating offline application repair archive and integrity manifest', flush=True)
    application = sorted([p for tree in [stage/'workbench', stage/'apparel-style-recolor'] for p in tree.rglob('*') if p.is_file() and not any(part in {'data','data-portable-test','__pycache__'} for part in p.relative_to(stage).parts)])
    application_zip = cache / 'application.zip'
    with zipfile.ZipFile(application_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in application:archive.write(path, path.relative_to(stage).as_posix())
    mutable = {'workbench/.env', 'workbench/config.json'}
    application_files = {path.relative_to(stage).as_posix(): sha(path) for path in application if path.relative_to(stage).as_posix() not in mutable}
    manifest = {'version': VERSION, 'platform': 'windows-x64', 'python_archive': {**py},
                'application_archive': {'filename': 'application.zip', 'sha256': sha(application_zip), 'urls': []},
                'python_files': python_files, 'dependency_files': dependency_files, 'application_files': application_files, 'wheels': wheels,
                'mutable_configuration': sorted(mutable), 'download_policy': 'bundled cache first; verified HTTPS domestic mirrors only'}
    (stage / 'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[5/6] Runtime, references, private configuration and checksum manifest ready', flush=True)
    summary = {'version': VERSION, 'platform': 'windows-x64', 'python_files': len(python_files), 'dependencies': len(wheels), 'dependency_files': len(dependency_files), 'application_files': len(application_files),
               'total_bytes': sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()), 'private_api_configuration_included': True, 'historical_jobs_included': False}
    (stage / 'package-info.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.zip:
        args.zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file() and not any(part in {'data', 'data-portable-test', 'logs', '__pycache__'} for part in path.relative_to(stage).parts):
                    archive.write(path, stage.name + '/' + path.relative_to(stage).as_posix())
        print('[6/6] Distribution ZIP:', str(args.zip), 'bytes:', args.zip.stat().st_size, 'SHA256:', sha(args.zip), flush=True)
    else:
        print('[6/6] Staging complete; extract/start verification required before final ZIP.', flush=True)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':raise SystemExit(main())
