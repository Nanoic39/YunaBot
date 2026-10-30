"""Loopback-only persistent computer browser bridge; never logs request bodies."""
import asyncio
import base64
import uuid
import time
import hmac
import http.client
import ipaddress
import json
import os
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

from aiohttp import web
from playwright.async_api import async_playwright, Error as BrowserError

ROOT = Path(os.environ.get('YUNA_COMPUTER_ROOT', '/YunaGames/yuna-computer'))
WIDTH, HEIGHT = 1280, 800
KEYS = {4:'Escape', 66:'Enter', 67:'Backspace', 61:'Tab', 19:'ArrowUp', 20:'ArrowDown', 21:'ArrowLeft', 22:'ArrowRight'}


def public_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None,443):
        raise ValueError('仅允许公网HTTPS网址')
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
        raise ValueError('禁止访问内网或元数据地址')
    return p, addresses[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=20, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address,443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch(url, method, headers, body):
    p, address = public_url(url)
    if method not in ('GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS') or len(body or b'') > 4*1024*1024:
        raise ValueError('请求超出限制')
    headers = {k:v for k,v in headers.items() if k.lower() not in ('host','connection','content-length','accept-encoding','proxy-authorization','x-computer-token','x-phone-token')}
    headers.update({'Host':p.hostname,'Accept-Encoding':'identity'})
    conn = PinnedHTTPS(p.hostname,address)
    try:
        conn.request(method,urlunsplit(('', '',p.path or '/',p.query,'')),body=body,headers=headers)
        r=conn.getresponse(); data=r.read(16*1024*1024+1)
        if len(data)>16*1024*1024: raise ValueError('响应过大')
        result={}
        for k,v in r.getheaders():
            k=k.lower()
            if k in ('transfer-encoding','content-length','connection'):continue
            result[k]=result[k]+ ('\n' if k=='set-cookie' else ', ')+v if k in result else v
        return r.status,result,data
    finally:conn.close()


DOM = r'''() => {
 const state=window.__yunaElements ||= {prefix:Date.now().toString(36)+'-'+Math.random().toString(36).slice(2), counter:0, ids:new WeakMap(), refs:new Map()};
 state.refs.clear();
 const all=[...document.querySelectorAll('input,textarea,[contenteditable="true"],button,a,select,[role="button"],label,h1,h2,h3,h4,h5,h6,p,li,span,div')];
 return all.map((e,i)=>{
  const b=e.getBoundingClientRect(), s=getComputedStyle(e);
  if(b.width<1||b.height<1||b.bottom<=0||b.right<=0||b.top>=innerHeight||b.left>=innerWidth||s.visibility==='hidden'||s.display==='none')return null;
  const edit=e.matches('input,textarea,[contenteditable="true"]');
  const ownText=[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
  if(!edit&&e.matches('span,div')&&e.children.length&&!ownText)return null;
  const label=e.getAttribute('aria-label')||e.getAttribute('placeholder')||'';
  const hint=[e.id,e.getAttribute('name'),e.getAttribute('type'),e.getAttribute('autocomplete'),label].filter(Boolean).join(' ');
  const text=edit?(e.value??e.innerText??''):(e.innerText||e.textContent||'').trim().slice(0,1600);
  if(!edit&&!text&&!label)return null;
  let record=state.ids.get(e);
  if(!record){record={id:state.prefix+':'+(++state.counter),revision:0};state.ids.set(e,record);}
  if(record.text!==text||record.label!==label){record.revision++;record.text=text;record.label=label;}
  const ref=record.id+':'+record.revision;
  state.refs.set(ref,{element:e,text,label,tag:e.tagName});
  return {id:'dom:'+record.id+':'+hint, ref, href:e.matches('a[href]')&&e.href.startsWith('https://')?e.href.slice(0,1500):'', text, label, edit,password:e.type==='password',focused:e===document.activeElement,
   bounds:[Math.max(0,Math.floor(b.left)),Math.max(0,Math.floor(b.top)),Math.min(innerWidth,Math.ceil(b.right)),Math.min(innerHeight,Math.ceil(b.bottom))]};
 }).filter(Boolean).slice(0,700);
}'''


class Computer:
    async def start(self):
        self.lock=asyncio.Lock();self.network_slots=asyncio.Semaphore(6);self.network_errors=[];self.network_events=[]
        try:self.paused_sites=json.loads((ROOT/'config/paused-sites.json').read_text())
        except (OSError,ValueError):self.paused_sites={}
        self.pw=await async_playwright().start()
        self.context=await self.pw.chromium.launch_persistent_context(str(ROOT/'profile'),headless=False,
            chromium_sandbox=True,viewport={'width':WIDTH,'height':HEIGHT},service_workers='block',
            accept_downloads=False,args=['--kiosk','--window-position=0,0','--window-size=1280,800','--disable-quic',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        self.context.on('page',lambda p:p.on('download',lambda d:asyncio.create_task(d.cancel())))
        await self.context.route('**/*',self.route)
        await self.context.route_web_socket('**/*',lambda route:route.close())
        self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()
        if self.page.url == 'about:blank':
            try:
                resume=json.loads((ROOT/'config/browser-session.json').read_text())['url']
                if self.site_paused(urlsplit(resume).hostname):raise ValueError('站点冷却中')
                await asyncio.to_thread(public_url,resume)
                await self.page.goto(resume,wait_until='commit',timeout=25000)
            except Exception:
                pass  # A failed restore must not prevent owner takeover.
        def remember(frame):
            if frame!=frame.page.main_frame:return
            location=urlsplit(frame.url)
            if location.scheme!='https' or not location.hostname:return
            # Never persist query strings/fragments (may contain login tokens).
            url=urlunsplit(('https',location.netloc,location.path,'',''))
            target=ROOT/'config/browser-session.json'
            tmp=target.with_suffix('.tmp')
            tmp.write_text(json.dumps({'url':url}));tmp.chmod(0o600);tmp.replace(target)
        for page in self.context.pages:page.on('framenavigated',remember)
        self.context.on('page',lambda page:page.on('framenavigated',remember))
        if self.page.url == 'about:blank':await self.page.set_content('<html lang="zh"><title>Yuna 虚拟电脑</title><body style="background:#182335;color:#eef;font:24px sans-serif;padding:60px"><h1>Yuna 虚拟电脑已就绪</h1><p>独立 Linux 显示器 · 持久浏览器 · QQ 信息协助</p><p>等待授权任务。网页内容不构成操作授权。</p></body></html>')

    def site_paused(self,host):
        return any((host==domain or (host or '').endswith('.'+domain)) and until>time.time() for domain,until in getattr(self,'paused_sites',{}).items())

    async def route(self,route):
        r=route.request
        if self.site_paused(urlsplit(r.url).hostname):return await route.abort()
        if r.url.startswith(('data:','blob:')):return await route.continue_()
        try:
            async with self.network_slots:
                status,headers,body=await asyncio.to_thread(fetch,r.url,r.method,await r.all_headers(),r.post_data_buffer)
            self.network_events.append({'host':urlsplit(r.url).hostname,'resource':r.resource_type,'status':status,'bytes':len(body)})
            self.network_events=self.network_events[-12:]
            await route.fulfill(status=status,headers=headers,body=body)
            self.network_events[-1]['fulfilled']=True
        except Exception as error:
            self.network_errors.append({'host':urlsplit(r.url).hostname,'type':type(error).__name__,'resource':r.resource_type})
            self.network_errors=self.network_errors[-12:]
            await route.abort()

    async def current(self):
        pages=[p for p in self.context.pages if not p.is_closed()]
        if not pages:self.page=await self.context.new_page()
        elif self.page.is_closed() or pages[-1]!=self.page:self.page=pages[-1]
        return self.page

    async def ui(self):
        page=await self.current()
        location=urlsplit(page.url)
        current_url=urlunsplit((location.scheme,location.hostname or '',location.path,'','')) if location.scheme in ('http','https') else ''
        root=ET.Element('hierarchy',rotation='0',current_url=current_url)
        for n in await page.evaluate(DOM):
            b=n['bounds'];ET.SubElement(root,'node',{
                'link-url':n.get('href',''),'element-ref':n['ref'],'resource-id':n['id'],'package':'browser','class':'android.widget.EditText' if n['edit'] else 'browser.Element',
                'text':n['text'],'content-desc':n['label'],'hint':n['label'],
                'focused':str(n['focused']).lower(),'password':str(n['password']).lower(),
                'bounds':f'[{b[0]},{b[1]}][{b[2]},{b[3]}]'})
        return ET.tostring(root,encoding='unicode')

    async def observe(self):
        # One lock covers capture and actions; retry if page navigation/DOM changed mid-capture.
        for _ in range(3):
            try:
                page=await self.current()
                before=await self.ui()
                image=await page.screenshot(type='png',timeout=10000,animations='disabled',
                    mask=[page.locator('input,textarea,[contenteditable=true]')])
                after=await self.ui()
                if before==after and page==await self.current():
                    return {'ok':True,'ui':after,'screenshot':base64.b64encode(image).decode(),
                            'activity':(await page.title())[:120],'size':'1280x800',
                            'observation_id':uuid.uuid4().hex}
            except BrowserError:
                await asyncio.sleep(0.3)
        raise ValueError('页面持续变化，请稍后重新观察')

    async def click_ref(self,page,ref):
        if not isinstance(ref,str) or len(ref)>100:raise ValueError('控件引用无效')
        handle=await page.evaluate_handle(r"""ref => {
            const r=window.__yunaElements?.refs.get(ref);
            if(!r||!r.element.isConnected)return null;
            const e=r.element,edit=e.matches('input,textarea,[contenteditable="true"]');
            const text=edit?(e.value??e.innerText??''):(e.innerText||e.textContent||'').trim().slice(0,1600);
            const label=e.getAttribute('aria-label')||e.getAttribute('placeholder')||'';
            return text===r.text&&label===r.label&&e.tagName===r.tag?e:null;
        }""",ref)
        try:
            element=handle.as_element()
            if element is None:raise ValueError('控件已变化，必须重新观察')
            # Playwright waits for visibility, stability, enabled state and hit testing.
            # Never force-click through an overlay or retry with old coordinates.
            await element.click(timeout=5000)
        finally:await handle.dispose()

    async def action(self,d):
        page=await self.current();a=d.get('action')
        if a=='pause_site':
            host=str(d.get('host') or '').lower()
            if not host or not all(c.isalnum() or c in '.-' for c in host):raise ValueError('站点无效')
            seconds=max(3600,min(86400,int(d.get('seconds',21600))))
            self.paused_sites=getattr(self,'paused_sites',{})
            self.paused_sites[host]=time.time()+seconds
            target=ROOT/'config/paused-sites.json';tmp=target.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.paused_sites));tmp.chmod(0o600);tmp.replace(target)
            for p in self.context.pages:
                if self.site_paused(urlsplit(p.url).hostname):await p.goto('about:blank')
            return '站点进入冷却，已停止页面后台请求'
        if a=='begin_reading':
            if getattr(self,'owner_context',None):raise ValueError('已有独立阅读环境')
            context=await self.context.browser.new_context(viewport={'width':WIDTH,'height':HEIGHT},
                service_workers='block',accept_downloads=False)
            try:
                await context.route('**/*',self.route)
                await context.route_web_socket('**/*',lambda route:route.close())
                context.on('page',lambda p:p.on('download',lambda d:asyncio.create_task(d.cancel())))
                reading_page=await context.new_page()
            except Exception:
                await context.close();raise
            self.owner_context,self.owner_page=self.context,self.page
            self.context,self.page=context,reading_page
            return '已打开无登录态的独立阅读窗口，原来的页面保留'
        if a=='end_reading':
            if getattr(self,'owner_context',None):
                reading=self.context
                self.context,self.page=self.owner_context,self.owner_page
                self.owner_context=self.owner_page=None
                await reading.close()
                await self.page.bring_to_front()
            return '已关闭独立阅读窗口'
        if a=='ui':return await self.ui()
        if a=='apps':return 'browser: 持久Chromium浏览器'
        if a=='launch':
            if d.get('package')!='browser':raise ValueError('仅可启动browser')
            await page.bring_to_front()
        elif a=='open_url':
            await asyncio.to_thread(public_url,str(d.get('url') or ''))
            await page.goto(d['url'],wait_until='commit',timeout=25000)
        elif a=='tap':
            x,y=int(d['x']),int(d['y'])
            if not(0<=x<WIDTH and 0<=y<HEIGHT):raise ValueError('点击超出屏幕')
            if d.get('element_ref'):await self.click_ref(page,d['element_ref'])
            else:await page.mouse.click(x,y)
        elif a=='swipe':
            await page.mouse.move(max(0,min(WIDTH-1,int(d['x1']))),max(0,min(HEIGHT-1,int(d['y1']))))
            await page.mouse.wheel(int(d['x1'])-int(d['x2']),int(d['y1'])-int(d['y2']))
        elif a=='drag':
            points=[int(d[k]) for k in ('x1','y1','x2','y2')]
            if not all(0<=v<(WIDTH if i%2==0 else HEIGHT) for i,v in enumerate(points)):raise ValueError('拖动超出屏幕')
            await page.mouse.move(points[0],points[1]);await page.mouse.down()
            try:await page.mouse.move(points[2],points[3],steps=20)
            finally:await page.mouse.up()
        elif a=='key':
            if int(d['keycode'])==3:return '请用open_url打开主页'
            key=KEYS.get(int(d['keycode']))
            if not key:raise ValueError('按键未开放')
            await page.keyboard.press(key)
        elif a=='text':
            value=d.get('text')
            if not isinstance(value,str) or len(value)>2000:raise ValueError('输入无效')
            if d.get('target'):
                tree=ET.fromstring(await self.ui())
                nodes=[n for n in tree if n.get('focused')=='true' and 'EditText' in n.get('class','')]
                if len(nodes)!=1 or [nodes[0].get(k,'') for k in ('resource-id','bounds','package','class')]!=d['target']:
                    raise ValueError('输入焦点发生变化，未填写秘密')
            focused=page.locator(':focus')
            if not await focused.count() or not await focused.evaluate("e=>e.matches('input,textarea,[contenteditable=true]')"):
                raise ValueError('当前没有可输入的焦点')
            if d.get('replace'):await focused.fill(value)
            else:await page.keyboard.insert_text(value)
            return '已输入（不回显）'
        else:raise ValueError('电脑动作不支持')
        return '动作已执行，请观察结果'


async def main():
    token=(ROOT/'config/token').read_text().strip()
    if len(token)<32:raise ValueError('电脑令牌配置无效')
    computer=Computer();await computer.start()
    @web.middleware
    async def auth(request,handler):
        supplied=request.headers.get('X-Phone-Token','')
        if not hmac.compare_digest(supplied,token):return web.json_response({'ok':False,'error':'未授权'},status=403)
        async with computer.lock:
            try:return await handler(request)
            except Exception as error:return web.json_response({'ok':False,'error':'电脑操作失败：'+type(error).__name__+'，请重新观察当前界面'},status=409)
    app=web.Application(middlewares=[auth],client_max_size=64*1024)
    async def health(request):
        page=await computer.current()
        return web.json_response({'ok':True,'environment':'computer','boot':'1','size':'1280x800','activity':(await page.title())[:120], 'network_errors':computer.network_errors,'network_events':computer.network_events})
    async def screenshot(request):
        page=await computer.current()
        # 管理端截图也遮蔽所有编辑框，秘密不能经截图接口泄漏。
        return web.Response(body=await page.screenshot(type='png',timeout=10000,animations='disabled',mask=[page.locator('input,textarea,[contenteditable=true]')]),content_type='image/png')
    async def observe(request):return web.json_response(await computer.observe())
    app.router.add_get('/observe',observe)
    async def action(request):return web.json_response({'ok':True,'output':await computer.action(await request.json())})
    app.router.add_get('/health',health);app.router.add_get('/screenshot',screenshot);app.router.add_post('/action',action)
    runner=web.AppRunner(app,access_log=None);await runner.setup()
    await web.TCPSite(runner,'127.0.0.1',8766).start()
    try:await asyncio.Event().wait()
    finally:await runner.cleanup();await computer.context.close();await computer.pw.stop()


if __name__=='__main__':asyncio.run(main())
