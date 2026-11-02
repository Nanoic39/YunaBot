"""Tiny phone-side receiver: accept latest ADB IP/ports and update the bridge."""
from __future__ import annotations
import hmac, ipaddress, json, os, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_FILE = '/YunaGames/Android/bridge/token'
ENV = '/YunaGames/Android/bridge/phone.env'
ADB = '/usr/bin/adb'
TOKEN = open(TOKEN_FILE).read().strip()
STATUS = '/YunaGames/Android/bridge/endpoint-status.json'
LOCK = threading.Lock()

def status(**fields):
    """Connection metadata only; never persist authentication headers."""
    temporary = STATUS + '.tmp'
    with open(temporary, 'w') as handle:
        json.dump({'time': time.time(), **fields}, handle)
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATUS)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ('/adb', '/api/phone/adb'):
            return self._json(404, {'ok': False})
        if not hmac.compare_digest(self.headers.get('X-Phone-Token', ''), TOKEN):
            return self._json(401, {'ok': False})
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if not 1 <= length <= 8192:
                return self._json(400, {'ok': False, 'error': 'body_size'})
            data = json.loads(self.rfile.read(length) or b'{}')
            ip = str(data.get('ip') or '')
            try:
                address = ipaddress.ip_address(ip)
            except ValueError:
                return self._json(400, {'ok': False, 'error': 'ip'})
            # This receiver only connects to the enrolled private phone network.
            if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
                return self._json(400, {'ok': False, 'error': 'ip'})
            ports = []
            for value in (data.get('ports') or [])[:20]:
                try: port = int(value)
                except Exception: continue
                if 1024 <= port <= 65535 and port not in ports: ports.append(port)
            if not ports:
                return self._json(400, {'ok': False, 'error': 'ports'})
            if not LOCK.acquire(blocking=False):
                return self._json(409, {'ok': False, 'error': 'checking'})
            try:
                status(ip=ip, ports=ports, state='checking')
                started = time.monotonic()
                for port in ports:
                    if time.monotonic() - started > 20:
                        break
                    serial = f'{ip}:{port}'
                    try:
                        subprocess.run([ADB, 'connect', serial], capture_output=True, timeout=5)
                        state = subprocess.run([ADB, '-s', serial, 'get-state'], capture_output=True, text=True, timeout=5)
                    except subprocess.TimeoutExpired:
                        continue
                    if state.stdout.strip() == 'device':
                        env = open(ENV).read()
                        updated = re.sub(r'(?m)^ANDROID_SERIAL=.*$', 'ANDROID_SERIAL=' + serial, env)
                        changed = updated != env
                        if changed:
                            temporary = ENV + '.tmp'
                            with open(temporary, 'w') as handle: handle.write(updated)
                            os.chmod(temporary, 0o600)
                            os.replace(temporary, ENV)
                            subprocess.run(['systemctl', 'restart', 'yuna-phone-bridge'], capture_output=True, timeout=15)
                        status(ip=ip, ports=ports, state='connected', serial=serial, bridge_changed=changed)
                        return self._json(200, {'ok': True, 'serial': serial})
                status(ip=ip, ports=ports, state='no_adb_port')
            finally:
                LOCK.release()
            return self._json(404, {'ok': False, 'error': 'no_adb_port'})
        except Exception as error:
            return self._json(500, {'ok': False, 'error': type(error).__name__})

def _bridge_online():
    try:
        token = open(TOKEN_FILE).read().strip()
        import urllib.request, json as _json
        req = urllib.request.Request('http://127.0.0.1:8765/health', headers={'X-Phone-Token': token})
        return bool(_json.load(urllib.request.urlopen(req, timeout=3)).get('online'))
    except Exception:
        return False


def _scan_ports():
    """无线调试端口会变；只在桥离线时扫描，找到可用 ADB 后更新桥。"""
    ip = '100.66.73.40'
    def open_port(port):
        try:
            with socket.create_connection((ip, port), timeout=0.25):
                return port
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=300) as pool:
        ports = [p for p in pool.map(open_port, range(30000, 65536)) if p]
    for port in ports:
        serial = f'{ip}:{port}'
        subprocess.run([ADB, 'connect', serial], capture_output=True, timeout=5)
        state = subprocess.run([ADB, '-s', serial, 'get-state'], capture_output=True, text=True, timeout=5)
        if state.stdout.strip() == 'device':
            env = open(ENV).read()
            env = re.sub(r'(?m)^ANDROID_SERIAL=.*$', 'ANDROID_SERIAL=' + serial, env)
            open(ENV, 'w').write(env)
            subprocess.run(['systemctl', 'restart', 'yuna-phone-bridge'], capture_output=True, timeout=15)
            return serial
    return ''


def _scanner_loop():
    while True:
        try:
            if not _bridge_online():
                _scan_ports()
        except Exception:
            pass
        time.sleep(30)


if __name__ == '__main__':
    threading.Thread(target=_scanner_loop, daemon=True).start()
    ThreadingHTTPServer(('100.119.158.38', 8766), Handler).serve_forever()
