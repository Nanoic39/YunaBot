"""Run with the computer worker runtime: python -m unittest test_bridge."""
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from bridge import public_url, Computer, fetch

class NetworkTests(unittest.TestCase):
    def test_private_or_mixed_dns_is_rejected(self):
        for addresses in (['127.0.0.1'],['169.254.169.254'],['8.8.8.8','10.0.0.2']):
            records=[(2,1,6,'',(a,443)) for a in addresses]
            with patch('bridge.socket.getaddrinfo',return_value=records):
                with self.assertRaises(ValueError):public_url('https://example.com')

    def test_non_https_credentials_and_custom_ports_are_rejected(self):
        for url in ('file:///etc/passwd','http://example.com','https://name:secret@example.com','https://example.com:8766'):
            with self.assertRaises(ValueError):public_url(url)

    def test_public_dns_address_is_returned_for_pinned_connection(self):
        with patch('bridge.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
            self.assertEqual(public_url('https://example.com/a')[1],'8.8.8.8')

    def test_duplicate_headers_are_valid_and_cookie_headers_stay_separate(self):
        conn=MagicMock();response=conn.getresponse.return_value
        response.status=200;response.read.return_value=b'data'
        response.getheaders.return_value=[('Vary','Origin'),('Vary','Accept-Encoding'),('Content-Encoding','gzip'),('Set-Cookie','a=1'),('Set-Cookie','b=2')]
        with patch('bridge.public_url',return_value=(__import__('urllib.parse',fromlist=['urlsplit']).urlsplit('https://example.com'),'8.8.8.8')),patch('bridge.PinnedHTTPS',return_value=conn):
            _,headers,_=fetch('https://example.com','GET',{},None)
        self.assertEqual(headers['vary'],'Origin, Accept-Encoding')
        self.assertEqual(headers['set-cookie'],'a=1\nb=2')
        self.assertEqual(headers['content-encoding'],'gzip')

class InputTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_focus_never_receives_secret(self):
        computer=Computer();page=AsyncMock();computer.current=AsyncMock(return_value=page)
        computer.ui=AsyncMock(return_value='<hierarchy><node focused="true" class="android.widget.EditText" resource-id="other" /></hierarchy>')
        with self.assertRaises(ValueError):
            await computer.action({'action':'text','text':'synthetic-test-value','target':['original','','browser','android.widget.EditText'],'replace':True})
        page.keyboard.insert_text.assert_not_called()
        page.locator.assert_not_called()

    async def test_apk_install_is_rejected(self):
        computer=Computer();computer.current=AsyncMock(return_value=AsyncMock())
        with self.assertRaises(ValueError):await computer.action({'action':'install','path':'/tmp/test.apk'})

class StructuredClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_reference_fails_without_coordinate_fallback(self):
        computer=Computer();page=AsyncMock();computer.current=AsyncMock(return_value=page)
        handle=MagicMock();handle.as_element.return_value=None;handle.dispose=AsyncMock()
        page.evaluate_handle.return_value=handle
        with self.assertRaises(ValueError):
            await computer.action({'action':'tap','x':10,'y':10,'element_ref':'obsolete'})
        page.mouse.click.assert_not_called()
        handle.dispose.assert_awaited_once()

    async def test_ref_click_uses_actionability_and_disposes_handle(self):
        computer=Computer();page=AsyncMock();computer.current=AsyncMock(return_value=page)
        handle=MagicMock();element=AsyncMock();handle.as_element.return_value=element;handle.dispose=AsyncMock()
        page.evaluate_handle.return_value=handle
        await computer.action({'action':'tap','x':10,'y':10,'element_ref':'observed'})
        element.click.assert_awaited_once_with(timeout=5000)
        page.mouse.click.assert_not_called()

    async def test_atomic_observation_rejects_changing_dom(self):
        computer=Computer();page=AsyncMock();computer.current=AsyncMock(return_value=page)
        page.locator=MagicMock()
        computer.ui=AsyncMock(side_effect=['a','b']*3)
        with self.assertRaises(ValueError):await computer.observe()

class BackoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_domain_blocks_background_requests_before_network_fetch(self):
        import time
        computer=Computer();computer.paused_sites={'xiaoheihe.cn':time.time()+86400}
        route=AsyncMock();route.request=MagicMock(url='https://api.xiaoheihe.cn/anything')
        with patch('bridge.fetch') as network:
            await computer.route(route)
            route.abort.assert_awaited_once();network.assert_not_called()
