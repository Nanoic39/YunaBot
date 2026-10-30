"""Owner-only QQ login view; credentials stay on the server."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx

SNOW_ROOT = Path('/YunaNexus/SnowLuma')
QR_FILE = Path('/YunaNexus/Napcat/native-core/cache/qrcode.png')
_token = ''
_lock = asyncio.Lock()


def read_qr(path: Path = QR_FILE) -> dict:
    try:
        age = max(0, time.time() - path.stat().st_mtime)
        data = path.read_bytes()
        if age > 120:
            return {'qr': None, 'qr_age': int(age), 'message': '二维码已过期，等待 QQ 客户端自动更新。'}
        if len(data) > 256_000 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid image')
        return {'qr': 'data:image/png;base64,' + base64.b64encode(data).decode(), 'qr_age': int(age),
                'message': '请用需要接入的手机 QQ 扫码，并在手机上确认登录。'}
    except (OSError, ValueError):
        return {'qr': None, 'message': '暂未获取到登录二维码，正在等待 QQ 客户端。'}


async def snapshot() -> dict:
    result = {'logged_in': False, 'bridge_connected': False, 'account': None, 'qr': None}
    try:
        global _token
        async with _lock:
            async with httpx.AsyncClient(base_url='http://127.0.0.1:5099', timeout=5, trust_env=False) as client:
                for attempt in range(2):
                    if not _token:
                        credential = json.loads((SNOW_ROOT / '.operator/webui-access.json').read_text())
                        login = await client.post('/api/login', json={'password': credential['password']})
                        login.raise_for_status()
                        _token = login.json()['token']
                    response = await client.get('/api/processes', headers={'X-SnowLuma-Token': _token})
                    if response.status_code == 401 and attempt == 0:
                        _token = ''
                        continue
                    response.raise_for_status()
                    break
                processes = response.json().get('list', [])
        result['bridge_connected'] = any(p.get('connected') for p in processes)
        logged = next((p for p in processes if p.get('loggedIn')), None)
        if logged:
            result.update(logged_in=True, account=str(logged.get('uin') or ''), message='QQ 已登录；机器人连接状态见上方。')
            return result
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        result['message'] = '暂时无法读取 SnowLuma 登录状态。'
        return result
    result.update(read_qr())
    return result
