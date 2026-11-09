"""Portable Windows launcher. Local-only autostart, no global Python installation.

The release carries its own runtime and private internal configuration. Never print
credentials, signed URLs, tokens or the environment. --test-mode uses separate data
and disables Feishu/gates/notifications; it does not modify the distributed config.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

WORKBENCH = Path(__file__).resolve().parents[1]
PACKAGE = WORKBENCH.parent
sys.path.insert(0, str(WORKBENCH))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load_private_env() -> None:
    machine = {'WORKBENCH_DATA_DIR','WORKBENCH_HOST','WORKBENCH_PORT','WORKBENCH_SESSION_SECRET','WORKBENCH_AUTH_TOKEN','PORT'}
    for name in machine:
        os.environ.pop(name, None)
    for raw in (WORKBENCH / '.env').read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, _, value = line.partition('=')
        key = name.strip()
        if key not in machine:
            os.environ[key] = value.strip().strip('\"').strip("'")


def prepare_config(test_mode: bool = False) -> tuple[Path, dict]:
    load_private_env()
    cfg = json.loads((WORKBENCH / 'config.json').read_text(encoding='utf-8'))
    data = WORKBENCH / ('data-portable-test' if test_mode else 'data')
    data.mkdir(parents=True, exist_ok=True)
    access_path = data / 'portable-access.json'
    if access_path.exists():
        access = json.loads(access_path.read_text(encoding='utf-8'))
    else:
        access = {'auth_token': secrets.token_urlsafe(32), 'session_secret': secrets.token_urlsafe(40)}
        write_json(access_path, access)
    cfg['skill_root'] = str(PACKAGE / 'apparel-style-recolor')
    cfg['library'] = {**cfg.get('library', {}), 'dir': str(WORKBENCH / 'library')}
    cfg['pricing_file'] = str(WORKBENCH / 'pricing.json')
    cfg['data_dir'] = str(data)
    cfg.setdefault('server', {}).update(host='127.0.0.1', auth_token=access['auth_token'], session_secret=access['session_secret'], login_required=True)
    if test_mode:
        cfg['feishu'] = {'enabled': False}
        cfg.setdefault('defaults', {})['notify'] = {'enabled': False}
        cfg['defaults']['gate'] = {'enabled': False, 'provider': 'feishu'}
        cfg['defaults']['max_retries'] = 1
        for provider in cfg.get('providers', {}).values():
            provider['skip_gate'] = True
    os.environ['WORKBENCH_DATA_DIR'] = str(data)
    os.environ['WORKBENCH_HOST'] = '127.0.0.1'
    os.environ.pop('WORKBENCH_PORT', None)
    os.environ.pop('PORT', None)
    config_path = data / 'portable-config.json'
    write_json(config_path, cfg)
    return config_path, cfg


def environment_report(test_mode: bool = False) -> dict:
    import importlib.metadata
    import ssl
    import zlib
    import fastapi
    import uvicorn
    import httpx
    import python_multipart
    import pydantic_core
    from PIL import Image
    from skillbridge.config import load_env
    from skillbridge.providers import build_providers
    from skillbridge.skill import load_skill
    from skillbridge.library import merge_into_skill
    from skillbridge import feishu
    load_private_env()
    cfg = json.loads((WORKBENCH / 'config.json').read_text(encoding='utf-8'))
    env = load_env()
    skill = load_skill(PACKAGE / 'apparel-style-recolor')
    merge_into_skill(skill, WORKBENCH / 'library')
    providers = build_providers(cfg, env)
    required = [provider.info() for name, provider in providers.items() if name in {'a6api', 'terra'}]
    required.sort(key=lambda row: row['name'] != cfg.get('default_provider'))
    harness = cfg.get('defaults', {}).get('harness') or {}
    ai_ready = bool(env.get(harness.get('api_key_env', 'DEEPSEEK_API_KEY'), '').strip())
    feishu_ready = bool(feishu.load_config(cfg, env).ready()[0])
    blob = io.BytesIO()
    Image.new('RGB', (8, 8), 'white').save(blob, format='WEBP')
    assert Image.open(io.BytesIO(blob.getvalue())).size == (8, 8)
    assert zlib.decompress(zlib.compress(b'portable-check')) == b'portable-check'
    assert ssl.create_default_context()
    three = WORKBENCH / 'web/blackglass/vendor/three-0.160.1.min.js'
    assert three.is_file() and three.stat().st_size > 600_000
    report = {'ok': True, 'python': sys.version.split()[0], 'architecture': 'win-amd64',
              'types': len(skill.types), 'effect_images': sum(1 for garment in skill.types.values() for color in garment.colors if color.effect and color.effect.is_file()),
              'providers': [{'label': row['label'], 'ready': bool(row['ready'])} for row in required],
              'harness_configured': ai_ready, 'feishu_configured': feishu_ready,
              'three_bundled': True, 'webp_native': True,
              'versions': {name: importlib.metadata.version(name) for name in ['fastapi', 'uvicorn', 'httpx', 'python-multipart', 'Pillow', 'pydantic-core']}}
    if len(required) != 2 or not all(row['ready'] for row in required) or not ai_ready:
        raise RuntimeError('Internal API configuration incomplete')
    return report


def read_health(port: int, token: str = '') -> dict | None:
    try:
        request = urllib.request.Request(f'http://127.0.0.1:{port}/api/health', headers={'x-workbench-token': token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=1.0) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def browser_url(port: int, cfg: dict) -> str:
    return f'http://127.0.0.1:{port}/?token=' + urllib.parse.quote(cfg['server']['auth_token'], safe='')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--test-mode', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(environment_report(args.test_mode), ensure_ascii=False))
        return 0
    import msvcrt
    import uvicorn
    data = WORKBENCH / ('data-portable-test' if args.test_mode else 'data')
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / 'portable.lock').open('a+b')
    lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        state_path = data / 'portable-state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            access = json.loads((data / 'portable-access.json').read_text(encoding='utf-8'))
            health = read_health(int(state['port']))
            if health and Path(health.get('skill_root', '')).resolve() == (PACKAGE / 'apparel-style-recolor').resolve():
                print('工作台已在运行，正在打开已有窗口。', flush=True)
                if not args.no_open:
                    webbrowser.open(browser_url(int(state['port']), {'server': access}))
                lock.close();return 0
        print('工作台正在启动，请稍后再次双击；不会启动重复进程。', flush=True)
        lock.close();return 0
    config_path, cfg = prepare_config(args.test_mode)
    port = 0
    candidates = [args.port] if args.port else range(8787, 8888)
    for candidate in candidates:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', candidate));port = candidate;break
            except OSError:
                continue
    if not port:
        raise RuntimeError('No available local port')
    cfg['server']['port'] = port
    write_json(config_path, cfg)
    os.environ['WORKBENCH_PORT'] = str(port)
    write_json(data / 'portable-state.json', {'pid': os.getpid(), 'port': port, 'package': str(PACKAGE), 'version': '9.4.1'})
    from skillbridge.api import create_app
    app = create_app(str(config_path))
    def wait_ready() -> None:
        for _ in range(180):
            health = read_health(port)
            if health and health.get('ok'):
                print(f'\n启动完成：http://127.0.0.1:{port}/\n关闭此窗口即停止服务；重复双击会打开已有工作台。', flush=True)
                if not args.no_open:
                    webbrowser.open(browser_url(port, cfg))
                return
            time.sleep(.5)
        print('服务启动超时，请查看本窗口和日志。', flush=True)
    threading.Thread(target=wait_ready, daemon=True).start()
    print(f'正在启动工作台（本机端口 {port}）…', flush=True)
    try:
        uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', access_log=False)
    finally:
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:pass
        lock.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'启动检查未通过：{type(error).__name__}。请保留本窗口并联系内部维护人员；日志不输出API密钥。', file=sys.stderr)
        raise SystemExit(1)
