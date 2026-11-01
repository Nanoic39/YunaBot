"""受限的 Android 手机控制桥；仅监听 127.0.0.1，不提供任意 shell。"""
from __future__ import annotations
import base64, json, os, re, secrets, subprocess, time, shlex
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/YunaGames/Android"
TOKEN_FILE = ROOT + "/bridge/token"
AUDIT = ROOT + "/bridge/audit.jsonl"
APKS = ROOT + "/apks"
ADB = os.getenv("ADB_BIN") or (ROOT + "/sdk/platform-tools/adb")
# 真机可填 Tailscale/内网 IP:端口，例如 100.x.x.x:5555；不要求手机接在电脑上。
SERIAL = os.getenv("ANDROID_SERIAL") or os.getenv("PHONE_ADB_SERIAL") or "172.31.99.2:5555"
ENV = {**os.environ, "HOME": ROOT + "/home", "ANDROID_HOME": ROOT + "/sdk",
       "ANDROID_SDK_ROOT": ROOT + "/sdk", "ANDROID_USER_HOME": ROOT + "/home/.android",
       "ANDROID_AVD_HOME": ROOT + "/avd", "TMPDIR": ROOT + "/tmp",
       "PATH": ROOT + "/sdk/platform-tools:" + os.environ.get("PATH", "")}
ENV["ANDROID_SERIAL"] = SERIAL

def token():
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w") as f:
            f.write(secrets.token_urlsafe(32))
        os.chmod(TOKEN_FILE, 0o600)
    return open(TOKEN_FILE).read().strip()

TOKEN = token()

def _state(timeout=8):
    try:
        p = subprocess.run([ADB, "-s", SERIAL, "get-state"], env=ENV, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace").strip(), p.stderr.decode("utf-8", "replace").strip()
    except Exception as error:
        return 1, "", str(error)


def ensure_online():
    """按需连接无线 ADB；手机暂时离线时返回明确错误，由调用方决定重试。"""
    code, state, error = _state()
    if code == 0 and state == "device":
        return True
    if ":" in SERIAL:
        try:
            # 已连接但变为 offline 时，单纯 adb connect 不会重置传输，必须先断开。
            if state in ("offline", "unauthorized") or code != 0:
                subprocess.run([ADB, "disconnect", SERIAL], env=ENV, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=8)
            subprocess.run([ADB, "connect", SERIAL], env=ENV, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=12)
        except Exception:
            pass
        code, state, error = _state()
    if code == 0 and state == "device":
        return True
    detail = (error or state or "device offline")[:300]
    raise ValueError(f"手机离线或未授权（{SERIAL}）：{detail}")


def adb(*args, timeout=90):
    ensure_online()
    p = subprocess.run([ADB, "-s", SERIAL, *args], env=ENV, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        # 传输中断可能刚掉线，重连一次再试；不无限重试。
        ensure_online()
        p = subprocess.run([ADB, "-s", SERIAL, *args], env=ENV, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode != 0:
        raise ValueError(p.stderr.decode("utf-8", "replace")[-500:] or "adb failed")
    return p.stdout

def shell(*args, timeout=90):
    return adb("shell", shlex.join(args), timeout=timeout)

def audit(entry):
    entry = {"time": time.time(), **entry}
    with open(AUDIT, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def intval(value, name, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"{name} 超出范围")
    return value

class Handler(BaseHTTPRequestHandler):
    server_version = "YunaPhoneBridge/1.0"
    def log_message(self, fmt, *args):
        pass
    def send_json(self, code, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def auth(self):
        return self.headers.get("X-Phone-Token", "") == TOKEN
    def do_GET(self):
        if not self.auth():
            return self.send_json(401, {"ok": False, "error": "unauthorized"})
        try:
            if self.path == "/health":
                try:
                    boot = shell("getprop", "sys.boot_completed").decode().strip()
                    top = shell("dumpsys", "activity", "activities").decode("utf-8", "ignore")
                    activity = next((line.strip() for line in top.splitlines() if "mResumedActivity" in line), "")
                    size = shell("wm", "size").decode().strip()
                    return self.send_json(200, {"ok": True, "online": True, "serial": SERIAL,
                                                "boot": boot, "activity": activity, "size": size})
                except Exception as error:
                    return self.send_json(200, {"ok": True, "online": False, "serial": SERIAL,
                                                "error": str(error)[:300]})
            if self.path == "/observe":
                top = shell("dumpsys", "activity", "activities").decode("utf-8", "ignore")
                activity = next((line.strip() for line in top.splitlines() if "mResumedActivity" in line), "")
                size = shell("wm", "size").decode().strip()
                shell("uiautomator", "dump", "/sdcard/yuna-observe.xml", timeout=60)
                ui = shell("cat", "/sdcard/yuna-observe.xml", timeout=30).decode("utf-8", "ignore")
                if len(ui) > 1048576:
                    raise ValueError("界面结构超过安全大小限制")
                ET.fromstring(ui)
                raw = adb("exec-out", "screencap", "-p", timeout=90)
                return self.send_json(200, {"ok": True, "online": True, "screenshot": base64.b64encode(raw).decode("ascii"),
                                            "ui": ui, "size": size, "activity": activity})
            if self.path == "/screenshot":
                raw = adb("exec-out", "screencap", "-p", timeout=90)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            return self.send_json(404, {"ok": False, "error": "not_found"})
        except Exception as e:
            return self.send_json(500, {"ok": False, "error": str(e)[:500]})
    def do_POST(self):
        if not self.auth():
            return self.send_json(401, {"ok": False, "error": "unauthorized"})
        if self.path != "/action":
            return self.send_json(404, {"ok": False, "error": "not_found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 200_000:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            action = str(body.get("action") or "")
            if action == "tap":
                out = shell("input", "tap", str(intval(body.get("x"), "x", 0, 10000)),
                            str(intval(body.get("y"), "y", 0, 20000)))
            elif action == "swipe":
                out = shell("input", "swipe", *(str(intval(body.get(k), k, 0, 20000))
                            for k in ("x1", "y1", "x2", "y2")),
                            str(intval(body.get("duration", 300), "duration", 20, 5000)))
            elif action == "text":
                text = body.get("text")
                if not isinstance(text, str) or not text or len(text) > 2000:
                    raise ValueError("text 无效")
                if any(ord(ch) > 127 for ch in text):
                    # ADB shell input text 不支持中文；使用 ADB Keyboard 注入后恢复原输入法。
                    try:
                        old_ime = shell("settings", "get", "secure", "default_input_method").decode("utf-8", "ignore").strip()
                        shell("ime", "set", "com.android.adbkeyboard/.AdbIME")
                        adb("shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT", "--es", "msg", text)
                        time.sleep(0.4)
                        if old_ime and "adbkeyboard" not in old_ime.lower():
                            shell("ime", "set", old_ime)
                        out = b""
                        audit({"action": action, "ok": True, "method": "adbkeyboard"})
                        return self.send_json(200, {"ok": True, "output": ""})
                    except Exception:
                        pass
                if body.get("replace") is True:
                    shell("uiautomator", "dump", "/sdcard/yuna-input.xml", timeout=60)
                    current = shell("cat", "/sdcard/yuna-input.xml")
                    focused = [n for n in ET.fromstring(current).iter("node") if "EditText" in n.get("class", "") and n.get("focused") == "true"]
                    if len(focused) != 1:
                        raise ValueError("没有唯一聚焦的原生输入框")
                    node = focused[0]
                    identity = [node.get(k, "") for k in ("resource-id", "bounds", "package", "class")]
                    if body.get("target") != identity:
                        raise ValueError("输入框已变化，禁止填写")
                    count = len(node.get("text", ""))
                    if count > 2000: raise ValueError("当前输入过长，停止替换")
                    shell("input", "keyevent", "123", *(["67"] * (count + 1)))
                out = shell("input", "text", text.replace(" ", "%s"))
            elif action == "key":
                out = shell("input", "keyevent", str(intval(body.get("keycode"), "keycode", 0, 400)))
            elif action == "launch":
                pkg = str(body.get("package") or "")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{1,180}", pkg):
                    raise ValueError("package 无效")
                entry = shell("cmd", "package", "resolve-activity", "--brief", pkg).decode("utf-8", "replace").strip().splitlines()[-1]
                if not re.fullmatch(re.escape(pkg)+r"/[A-Za-z0-9_.$]+", entry):
                    raise ValueError("未找到已安装应用的启动入口")
                out = shell("am", "start", "-W", "-n", entry)
            elif action == "open_url":
                url = str(body.get("url") or "")
                if not re.fullmatch(r"https?://[^\s]{1,1900}", url):
                    raise ValueError("url 无效")
                out = shell("am", "start", "-a", "android.intent.action.VIEW", "-d", url)
            elif action == "install":
                path = os.path.realpath(str(body.get("path") or ""))
                if not path.startswith(APKS + "/") or not path.endswith(".apk"):
                    raise ValueError("只允许安装 apks 目录下的 APK")
                out = adb("install", "-r", path, timeout=180)
            elif action == "apps":
                out = shell("pm", "list", "packages", "-3")
            elif action == "ui":
                shell("uiautomator", "dump", "/sdcard/ui.xml", timeout=60)
                out = shell("cat", "/sdcard/ui.xml", timeout=30)
            elif action == "wait":
                seconds = intval(body.get("seconds", 2), "seconds", 1, 10)
                time.sleep(seconds)
                out = b""
            elif action == "wechat_ocr":
                raw = adb("exec-out", "screencap", "-p", timeout=60)
                ocr_file = "/tmp/yuna-wechat-ocr.png"
                with open(ocr_file, "wb") as fh:
                    fh.write(raw)
                proc = subprocess.run(["tesseract", ocr_file, "stdout", "-l", "chi_sim+eng",
                                       "--psm", "11", "tsv"], stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, timeout=30)
                rows = []
                for line in proc.stdout.decode("utf-8", "ignore").splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) < 12 or not parts[11].strip():
                        continue
                    try:
                        left, top, width, height = (int(parts[6]), int(parts[7]),
                                                    int(parts[8]), int(parts[9]))
                    except ValueError:
                        continue
                    if width <= 0 or height <= 0:
                        continue
                    rows.append({"text": parts[11][:200], "x": left, "y": top,
                                 "w": width, "h": height})
                return self.send_json(200, {"ok": True, "output": json.dumps(rows, ensure_ascii=False)})
            elif action == "wechat_notifications":
                raw = shell("dumpsys", "notification", "--noredact", timeout=30).decode("utf-8", "ignore")
                rows = []
                for chunk in re.split(r"\n\s*NotificationRecord\(", raw):
                    if "com.tencent.mm" not in chunk:
                        continue
                    title = re.search(r"android\.title=String \((.*?)\)", chunk)
                    text = re.search(r"android\.text=String \((.*?)\)", chunk)
                    if title:
                        rows.append({"title": title.group(1)[:120], "text": (text.group(1) if text else "")[:500]})
                return self.send_json(200, {"ok": True, "output": json.dumps(rows, ensure_ascii=False)})
            else:
                raise ValueError("未知 action")
            audit({"action": action, "ok": True})
            text = out.decode("utf-8", "ignore")
            if action == "ui":
                if len(text) > 1048576: raise ValueError("界面结构超过安全大小限制")
                ET.fromstring(text)  # 保留完整XML，禁止截断成执行器无法解析的半个节点。
            else:
                text = text[-2000:]
            return self.send_json(200, {"ok": True, "output": text})
        except Exception as e:
            audit({"action": "error", "ok": False, "error": str(e)[:200]})
            return self.send_json(400, {"ok": False, "error": str(e)[:500]})

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    server.serve_forever()
