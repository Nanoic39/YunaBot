"""拟真世界地图：固定布局、多路线、可拓展区域、天气、场景细节和突发事件。"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
NODE_DEFS = [
    ("home", "家", "area", None, False),
    ("bedroom", "卧室", "room", "home", False),
    ("living", "客厅", "room", "home", False),
    ("study", "书房", "room", "home", False),
    ("kitchen", "厨房", "room", "home", False),
    ("bathroom", "浴室", "room", "home", False),
    ("porch", "玄关", "room", "home", False),
    ("street_home", "家门口", "street", None, True),
    ("alley", "小巷", "street", None, True),
    ("main_street", "主街", "street", None, True),
    ("plaza", "广场", "plaza", None, True),
    ("convenience", "便利店", "shop", None, True),
    ("cafe", "咖啡馆", "shop", None, True),
    ("park", "公园", "park", None, True),
    ("school", "学校", "area", None, True),
    ("school_gate", "校门口", "street", "school", True),
    ("mall", "商场", "area", None, True),
    ("mall_1f", "商场一层", "shop", "mall", True),
    ("mall_2f", "商场二层", "shop", "mall", True),
    ("mall_food", "商场美食层", "shop", "mall", True),
    ("mall_shop", "商场小摊", "shop", "mall", True),
]
ROUTES = [
    ("bedroom", "living", 2, "穿过走廊", False),
    ("bedroom", "study", 1, "推门就到", False),
    ("living", "kitchen", 1, "绕到厨房", False),
    ("living", "porch", 2, "经过玄关", False),
    ("study", "porch", 2, "穿过客厅", False),
    ("kitchen", "porch", 1, "从餐厅过去", False),
    ("bedroom", "bathroom", 1, "走廊尽头", False),
    ("porch", "street_home", 2, "换鞋出门", True),
    ("street_home", "alley", 3, "走安静的小巷", True),
    ("street_home", "main_street", 5, "沿主街走", True),
    ("alley", "plaza", 7, "穿过旧墙和花坛", True),
    ("main_street", "plaza", 8, "经过一排店面", True),
    ("street_home", "convenience", 4, "拐角就到", True),
    ("plaza", "cafe", 4, "沿广场边", True),
    ("plaza", "park", 9, "过一座小桥", True),
    ("plaza", "mall_1f", 5, "从广场入口进", True),
    ("plaza", "school_gate", 15, "沿学校围墙", True),
    ("main_street", "school_gate", 10, "穿过斑马线", True),
    ("mall_1f", "mall_2f", 1, "坐扶梯上楼", False),
    ("mall_1f", "mall_shop", 2, "走到侧边小摊", False),
    ("mall_2f", "mall_food", 1, "上一层美食区", False),
]
NAME_TO_ID = {name: node_id for node_id, name, _kind, _parent, _out in NODE_DEFS}
NAME_TO_ID.update({"家": "home", "学校门口": "school_gate", "商场": "mall"})
DETAIL_DEFAULTS = {
    "bedroom": {"curtains": "半开", "painting": "床边", "photo": "书桌左上角", "snack_count": 4, "desk": "靠窗"},
    "living": {"curtains": "拉开", "sofa_blanket": "叠好", "snack_count": 3},
    "study": {"desk": "靠窗", "books": "按颜色排", "painting": "书架上方", "lamp": "关"},
    "kitchen": {"kettle": "在台面", "snack_count": 6},
}


def _now() -> int:
    return int(time.time())


class WorldMap:
    def __init__(self, path):
        self.path = path
        self.data = self._load()
        self._refresh_weather()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    raw.setdefault("nodes", {})
                    raw.setdefault("routes", [])
                    raw.setdefault("details", {})
                    raw.setdefault("lessons", [])
                    raw.setdefault("events", [])
                    raw.setdefault("current", "bedroom")
                    raw.setdefault("weather", {})
                    return raw
            except Exception:
                pass
        nodes = {}
        for node_id, name, kind, parent, outdoor in NODE_DEFS:
            nodes[node_id] = {"name": name, "kind": kind, "parent": parent, "outdoor": outdoor}
        routes = [
            {"from": a, "to": b, "minutes": minutes, "description": desc, "outdoor": outdoor}
            for a, b, minutes, desc, outdoor in ROUTES
        ]
        return {
            "version": 1,
            "nodes": nodes,
            "routes": routes,
            "details": {k: dict(v) for k, v in DETAIL_DEFAULTS.items()},
            "current": "bedroom",
            "weather": {},
            "lessons": [],
            "events": [],
            "updated": _now(),
        }

    def _save(self) -> None:
        self.data["updated"] = _now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    # ---------- weather ----------
    def _refresh_weather(self) -> None:
        now = _now()
        weather = self.data.get("weather") or {}
        if int(weather.get("until") or 0) > now:
            return
        moment = datetime.fromtimestamp(now, TZ)
        season = "summer" if 6 <= moment.month <= 8 else "winter" if moment.month in (12, 1, 2) else "spring"
        base = {"summer": 29, "winter": 8, "spring": 19}[season]
        condition = random.choices(
            ["clear", "cloudy", "rain", "hot", "cold"],
            weights=[0.38, 0.27, 0.18, 0.1 if season == "summer" else 0.03, 0.1 if season == "winter" else 0.02],
        )[0]
        if condition == "rain":
            base -= 4
            text = "小雨"
        elif condition == "hot":
            base += 6
            text = "闷热"
        elif condition == "cold":
            base -= 8
            text = "干冷"
        elif condition == "cloudy":
            base -= 1
            text = "多云"
        else:
            text = "晴"
        self.data["weather"] = {
            "condition": condition, "text": text,
            "temperature": base + random.randint(-2, 2),
            "until": now + random.randint(50, 120) * 60,
            "updated": now,
        }
        self._save()

    def weather(self) -> dict:
        self._refresh_weather()
        return dict(self.data.get("weather") or {})

    # ---------- map ----------
    def node(self, node_id: str) -> dict:
        return dict((self.data.get("nodes") or {}).get(str(node_id)) or {})

    def node_id_by_name(self, name: str) -> str:
        return NAME_TO_ID.get(str(name), str(name) if str(name) in (self.data.get("nodes") or {}) else "")

    def current(self) -> dict:
        node_id = str(self.data.get("current") or "bedroom")
        node = self.node(node_id)
        return {"id": node_id, **node, "weather": self.weather()}

    def set_current(self, node_id: str) -> dict:
        if str(node_id) in (self.data.get("nodes") or {}):
            self.data["current"] = str(node_id)
            self._save()
        return self.current()

    def routes_from(self, start: str) -> list[dict]:
        return [
            dict(route) for route in (self.data.get("routes") or [])
            if route.get("from") == start or route.get("to") == start
        ]

    def plan_routes(self, start: str, target: str, limit: int = 4) -> list[dict]:
        """返回最多 limit 条不同路线；每条包含 nodes/minutes/description。"""
        start, target = str(start), str(target)
        if start == target:
            return []
        graph = {}
        for route in self.data.get("routes") or []:
            a, b = str(route.get("from")), str(route.get("to"))
            graph.setdefault(a, []).append((b, route))
            graph.setdefault(b, []).append((a, route))
        paths = []

        def dfs(node, visited, edges):
            if len(paths) >= limit * 3:
                return
            if node == target:
                paths.append(list(edges))
                return
            for nxt, route in graph.get(node, []):
                if nxt in visited or len(edges) > 8:
                    continue
                dfs(nxt, visited | {nxt}, edges + [route])

        dfs(start, {start}, [])
        unique = []
        seen = set()
        for path in paths:
            minutes = sum(int(r.get("minutes") or 1) for r in path)
            names = [start] + [str(r.get("to") if str(r.get("from")) == start else r.get("from")) for r in path]
            key = tuple(sorted(str(r.get("from")) + ":" + str(r.get("to")) for r in path))
            if key in seen:
                continue
            seen.add(key)
            unique.append({"nodes": names, "minutes": minutes,
                           "description": " → ".join(str(r.get("description")) for r in path),
                           "weather_exposed": any(r.get("outdoor") for r in path)})
        unique.sort(key=lambda x: x["minutes"])
        return unique[:limit]

    def expand(self, parent: str, name: str, kind: str = "shop", *, routes: list[dict] | None = None) -> dict:
        """新增区域/店铺/街道；名称或节点冲突时拒绝，自动接回 parent。"""
        parent_id = self.node_id_by_name(parent) or str(parent)
        if parent_id not in (self.data.get("nodes") or {}):
            return {"ok": False, "error": "unknown_parent"}
        name = str(name or "").strip()
        if not name:
            return {"ok": False, "error": "empty_name"}
        if name in NAME_TO_ID or any(n.get("name") == name for n in self.data["nodes"].values()):
            return {"ok": False, "error": "already_exists"}
        node_id = "custom_" + str(abs(hash(name)) % 10_000_000)
        self.data["nodes"][node_id] = {"name": name, "kind": kind or "shop", "parent": parent_id, "outdoor": kind in {"street", "plaza", "park"}}
        NAME_TO_ID[name] = node_id
        self.data["routes"].append({"from": parent_id, "to": node_id, "minutes": 3, "description": f"去{name}", "outdoor": False})
        for route in routes or []:
            if not isinstance(route, dict) or not route.get("to"):
                continue
            target = self.node_id_by_name(str(route["to"])) or str(route["to"])
            if target in self.data["nodes"]:
                self.data["routes"].append({"from": node_id, "to": target, "minutes": max(1, int(route.get("minutes") or 4)),
                                            "description": str(route.get("description") or f"从{name}过去"), "outdoor": False})
        self._save()
        return {"ok": True, "id": node_id, "name": name, "parent": parent_id}

    def ensure_destination(self, name: str, parent: str = "plaza") -> str:
        """有需求但地图上没有的地方，在合理父区域下新增并接回主路网。"""
        name = str(name or "").strip()
        if not name:
            return ""
        known = self.node_id_by_name(name)
        if known:
            return known
        result = self.expand(parent, name, "shop")
        return str(result.get("id") or "") if result.get("ok") else ""

    def change_detail(self, node_id: str, key: str, value) -> dict:
        node_id = self.node_id_by_name(node_id) or str(node_id)
        if node_id not in (self.data.get("nodes") or {}):
            return {"ok": False, "error": "unknown_node"}
        self.data.setdefault("details", {}).setdefault(node_id, {})[str(key)] = value
        self._save()
        return {"ok": True, "node": node_id, "key": key, "value": value}

    # ---------- events / lessons ----------
    def _has_lesson(self, key: str) -> bool:
        return any(item.get("key") == key for item in self.data.get("lessons") or [])

    def _add_lesson(self, key: str, note: str) -> None:
        if not self._has_lesson(key):
            self.data.setdefault("lessons", []).append({"key": key, "note": note, "time": _now()})
            self.data["lessons"] = self.data["lessons"][-50:]

    def maybe_event(self, activity: str, location: str) -> dict | None:
        """低频生活突发事件；返回事件后由上层决定是否取消活动/进入分享池。"""
        activity = str(activity or "")
        node_id = self.node_id_by_name(location) or str(location)
        if node_id not in (self.data.get("nodes") or {}):
            return None
        # 同一天最多一个突发事件，且有全局冷却
        now = _now()
        recent = [e for e in (self.data.get("events") or []) if now - int(e.get("time") or 0) < 12 * 3600]
        if recent:
            return None
        today = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
        if any(datetime.fromtimestamp(int(e.get("time") or 0), TZ).strftime("%Y-%m-%d") == today for e in self.data.get("events") or []):
            return None
        hour = datetime.fromtimestamp(now, TZ).hour
        event = None
        if any(word in activity for word in ("吃", "便利店", "食堂", "小吃")):
            key = f"food_poison:{node_id}"
            if not self._has_lesson(key) and random.random() < 0.035:
                event = {
                    "kind": "food_poison", "node": node_id, "activity": activity,
                    "summary": "路边小吃不太干净，吃完肚子不舒服",
                    "detail": "在吃小吃之后肚子开始疼，只能取消接下来的安排，回家休息。",
                    "duration_minutes": 120, "energy_delta": -0.18,
                    "cancel_remaining": True, "lesson_key": key,
                    "lesson": "同一家不干净的小摊以后不要再买",
                    "share_hint": "有点无奈地吐槽吃坏肚子，不要点名真实商家。",
                    "simulated": True,
                }
        elif "整理" in activity and node_id in {"bedroom", "study"} and random.random() < 0.04:
            event = {
                "kind": "found_album", "node": node_id, "activity": activity,
                "summary": "整理房间翻到旧相册，忍不住看了起来",
                "detail": "本来在整理房间，翻到小时候的相册，坐在地上看了很久。",
                "duration_minutes": 40, "energy_delta": -0.02,
                "cancel_remaining": False, "lesson_key": "album:seen",
                "lesson": "", "share_hint": "分享翻到旧相册时的一点回忆感，不要编造具体照片内容。",
                "simulated": True,
            }
        elif any(word in activity for word in ("写", "画", "书签")) and random.random() < 0.025:
            event = {
                "kind": "found_book", "node": node_id, "activity": activity,
                "summary": "翻到一本有意思的旧书，思路被带跑了",
                "detail": "本来在做自己的东西，翻到一本旧书，结果看入了神。",
                "duration_minutes": 35, "energy_delta": -0.01,
                "cancel_remaining": False, "lesson_key": "",
                "lesson": "", "share_hint": "分享被旧书吸引、思路跑偏的小事。",
                "simulated": True,
            }
        if event:
            self.data.setdefault("events", []).append({
                "time": now, "kind": event["kind"], "node": node_id, "summary": event["summary"],
            })
            self.data["events"] = self.data["events"][-100:]
            if event.get("lesson_key") and event.get("lesson"):
                self._add_lesson(event["lesson_key"], event["lesson"])
            self._save()
            return event
        return None

    def prompt_block(self) -> str:
        current = self.current()
        weather = current.get("weather") or {}
        lines = [
            f"【世界地图】你现在在「{current.get('name') or '卧室'}」。",
            f"天气：{weather.get('text') or '晴'}，约 {weather.get('temperature')}°C。",
        ]
        routes = self.routes_from(current.get("id") or "")
        if routes:
            lines.append("附近可以去：" + "、".join(
                f"{self.node(r.get('to') if r.get('from') == current.get('id') else r.get('from')).get('name')}（{r.get('minutes')}分钟）"
                for r in routes[:8]
            ) + "。")
        details = (self.data.get("details") or {}).get(current.get("id") or "") or {}
        if details:
            lines.append("场景细节：" + "、".join(f"{k}={v}" for k, v in list(details.items())[:6]) + "。")
        if weather.get("condition") in ("rain", "storm"):
            lines.append("下雨时你不太想出门，但也不是绝对不会；如果必须出门会带伞。")
        elif weather.get("condition") == "hot":
            lines.append("天气闷热，你更愿意待在室内，出门会找有空调的路线。")
        elif weather.get("condition") == "cold":
            lines.append("天气偏冷，你会想穿暖一点，减少长时间户外活动。")
        return "\n".join(lines)

    def snapshot(self) -> dict:
        current = self.current()
        return {
            "current": current,
            "nodes": [{"id": node_id, **dict(node)} for node_id, node in (self.data.get("nodes") or {}).items()],
            "routes": self.routes_from(current.get("id") or ""),
            "all_routes": [dict(route) for route in (self.data.get("routes") or [])],
            "weather": self.weather(),
            "details": dict((self.data.get("details") or {}).get(current.get("id") or "") or {}),
            "lessons": list(self.data.get("lessons") or [])[-20:],
            "events": list(self.data.get("events") or [])[-10:],
        }
