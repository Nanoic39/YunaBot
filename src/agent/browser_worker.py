"""节点上的公开网页只读执行器。网络由带DNS校验的固定IP HTTPS请求承载。"""
from __future__ import annotations

import asyncio
import base64
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .control import ControlError
from .nodes import validate_url

MAX_BODY = 2 * 1024 * 1024


def public_address(host):
    addresses = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ControlError('DNS解析包含非公网地址')
    return addresses[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=8, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_public(url, domains):
    host = validate_url(url, domains)
    # 校验与实际连接使用同一IP，避免“先验证DNS、后由浏览器重新解析”的重绑定漏洞。
    connection = PinnedHTTPS(host, public_address(host))
    parsed = urlsplit(url)
    target = urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    try:
        connection.request('GET', target, headers={'Host': host, 'User-Agent': 'YunaReadOnly/1.0', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ControlError('网页响应超过2MiB')
        headers = {key.lower(): value for key, value in response.getheaders()
                   if key.lower() in ('content-type', 'location')}
        # 重定向由浏览器发起下一次路由，下一跳仍须重新校验域名和IP。
        headers['content-security-policy'] = "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; script-src 'none'; connect-src 'none'; frame-src 'none'; form-action 'none'"
        return response.status, headers, body
    finally:
        connection.close()


async def read_page(job):
    from playwright.async_api import async_playwright

    requests = 0
    total_bytes = 0
    blocked_main = []
    semaphore = asyncio.Semaphore(3)
    result = {'ok': False, 'url': job['url'], 'title': '', 'text': '', 'screenshot': '', 'error': ''}
    async with async_playwright() as playwright:
        # 不暴露调试端口；Chromium的沙箱必须可用，失败时不自动添加--no-sandbox。
        browser = await playwright.chromium.launch(executable_path=playwright.chromium.executable_path, headless=True, chromium_sandbox=True)
        context = await browser.new_context(java_script_enabled=False, service_workers='block', accept_downloads=False,
                                            viewport={'width': 1100, 'height': 800})
        async def route_request(route):
            nonlocal requests, total_bytes
            request = route.request
            requests += 1
            if request.method not in ('GET', 'HEAD') or request.resource_type not in ('document', 'stylesheet', 'image', 'font') or requests > 25:
                await route.abort()
                return
            try:
                async with semaphore:
                    status, headers, body = await asyncio.to_thread(fetch_public, request.url, job['allowed_domains'])
                    total_bytes += len(body)
                    if total_bytes > 8 * MAX_BODY:
                        raise ControlError('页面累计响应超过16MiB')
                    await route.fulfill(status=status, headers=headers, body=body)
            except Exception:
                if request.is_navigation_request():
                    blocked_main.append(True)
                await route.abort()
        await context.route('**/*', route_request)
        page = await context.new_page()
        try:
            async with asyncio.timeout(min(30, job.get('timeout_seconds', 30))):
                response = await page.goto(job['url'], wait_until='load', timeout=25000)
                validate_url(page.url, job['allowed_domains'])
                if blocked_main or not response or response.status >= 400:
                    raise ControlError('网页未成功读取或导航被拒绝')
                result.update(ok=True, url=page.url, title=(await page.title())[:300],
                              text=(await page.locator('body').inner_text(timeout=3000))[:20000])
                screenshot = await page.screenshot(type='png', full_page=False, timeout=3000)
                if len(screenshot) <= MAX_BODY:
                    result['screenshot'] = base64.b64encode(screenshot).decode()
        except Exception:
            result.update(ok=False, error='公开网页读取失败、超时或被白名单/网络规则阻止')
        finally:
            await context.close()
            await browser.close()
    return result


def load_config(path):
    path = Path(path)
    config = json.loads(path.read_text())
    url = urlsplit(config['control_url'])
    if not ((url.scheme == 'http' and url.hostname in ('127.0.0.1', '::1')) or url.scheme == 'https'):
        raise ValueError('控制端只允许本机SSH隧道HTTP或经过验证的HTTPS')
    return config


async def run(path):
    config = load_config(path)
    base = config['control_url'].rstrip('/')
    headers = {'Authorization': 'Bearer ' + config['token'], 'X-Yuna-Admin': '1'}
    # 不使用浏览器上下文调用控制端，控制凭据不会进入网页进程或页面上下文。
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15, follow_redirects=False, trust_env=False) as client:
        while True:
            try:
                response = await client.post('/api/agent/worker/claim')
                response.raise_for_status()
                job = response.json()['job']
                if job:
                    try:
                        result = await read_page(job)
                    except Exception:
                        result = {'ok': False, 'url': job['url'], 'title': '', 'text': '', 'screenshot': '',
                                  'error': '浏览器无法启动，请检查沙箱与运行依赖'}
                    for attempt in range(3):
                        try:
                            completed = await client.post('/api/agent/worker/complete', json={
                                'job_id': job['id'], 'lease': job['lease'], 'result': result})
                            completed.raise_for_status()
                            break
                        except httpx.HTTPError:
                            if attempt == 2:
                                print('任务结果未能送达；不自动重新执行任务', flush=True)
                            await asyncio.sleep(2)
            except (httpx.HTTPError, KeyError, ValueError):
                print('控制端连接暂不可用或节点未启用', flush=True)
            await asyncio.sleep(5)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config))
