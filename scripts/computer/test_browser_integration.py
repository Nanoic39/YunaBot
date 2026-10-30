"""Real Chromium fixture tests. Uses an isolated temporary profile, never live accounts."""
import asyncio
import unittest
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright
from bridge import Computer


class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.launch(headless=True,channel="chromium",chromium_sandbox=True)
        self.context=await self.browser.new_context(viewport={'width':1280,'height':800})
        self.page=await self.context.new_page()
        self.computer=Computer();self.computer.context=self.context;self.computer.page=self.page
        await self.page.set_content('<button id="a" onclick="this.dataset.clicked=1">查看资料</button><input placeholder="搜索">')

    async def asyncTearDown(self):
        await self.browser.close();await self.pw.stop()

    async def ref(self,text):
        tree=ET.fromstring(await self.computer.ui())
        return next(n.get('element-ref') for n in tree if n.get('text')==text)

    async def test_moving_control_uses_element_not_old_position(self):
        ref=await self.ref('查看资料')
        await self.page.locator('#a').evaluate("e=>e.style.marginLeft='500px'")
        await self.computer.action({'action':'tap','x':12,'y':12,'element_ref':ref})
        self.assertEqual(await self.page.locator('#a').get_attribute('data-clicked'),'1')

    async def test_changed_label_rejects_stale_reference_after_refresh(self):
        ref=await self.ref('查看资料')
        await self.page.locator('#a').evaluate("e=>e.textContent='确认付款'")
        await self.computer.ui()
        with self.assertRaises(ValueError):
            await self.computer.action({'action':'tap','x':12,'y':12,'element_ref':ref})
        self.assertIsNone(await self.page.locator('#a').get_attribute('data-clicked'))

    async def test_recreated_element_does_not_reuse_reference(self):
        ref=await self.ref('查看资料')
        await self.page.locator('#a').evaluate('e=>e.outerHTML=e.outerHTML')
        with self.assertRaises(ValueError):await self.computer.click_ref(self.page,ref)

    async def test_overlay_is_not_force_clicked(self):
        from playwright.async_api import TimeoutError
        ref=await self.ref('查看资料')
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:999\">遮罩</div>')")
        with self.assertRaises(TimeoutError):await self.computer.click_ref(self.page,ref)
        self.assertIsNone(await self.page.locator('#a').get_attribute('data-clicked'))

    async def test_observation_contains_matching_ui_and_masked_png(self):
        import base64
        result=await self.computer.observe()
        self.assertTrue(base64.b64decode(result['screenshot']).startswith(b'\x89PNG'))
        self.assertIn('查看资料',result['ui'])
        self.assertTrue(result['observation_id'])

    async def test_navigation_invalidates_old_reference(self):
        ref=await self.ref('查看资料')
        await self.page.goto('about:blank')
        await self.page.set_content('<button>查看资料</button>')
        with self.assertRaises(ValueError):await self.computer.click_ref(self.page,ref)

    async def test_reading_context_has_no_account_cookies_and_restores_owner_tab(self):
        await self.context.add_cookies([{'name':'account','value':'synthetic','domain':'example.com','path':'/','secure':True}])
        owner_page=self.page
        await self.computer.action({'action':'begin_reading'})
        self.assertEqual(await self.computer.context.cookies(),[])
        self.assertFalse(owner_page.is_closed())
        await self.computer.action({'action':'end_reading'})
        self.assertIs(await self.computer.current(),owner_page)
        self.assertEqual((await self.context.cookies())[0]['name'],'account')
