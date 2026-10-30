"""时间/空间连续的生活状态机。

活动由程序推进，模型只能提出动作；聊天只读取快照，不会因为一句话换掉当前活动。
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from nonebot.log import logger

TZ_OFFSET = 8


def _now() -> int:
    return int(time.time())


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _hour(ts: int | None = None) -> int:
    return datetime.fromtimestamp((ts or _now()) + TZ_OFFSET * 3600, timezone.utc).hour


LOCATIONS = {
    "卧室": {"kind": "home", "indoor": True},
    "客厅": {"kind": "home", "indoor": True},
    "书房": {"kind": "home", "indoor": True},
    "厨房": {"kind": "home", "indoor": True},
    "便利店": {"kind": "outside", "indoor": False},
    "咖啡馆": {"kind": "outside", "indoor": True},
    "公园": {"kind": "outside", "indoor": False},
    "学校": {"kind": "outside", "indoor": True},
}

TRAVEL_MINUTES = {
    ("卧室", "客厅"): 1,
    ("卧室", "书房"): 1,
    ("卧室", "厨房"): 1,
    ("客厅", "厨房"): 1,
    ("客厅", "书房"): 2,
    ("书房", "厨房"): 2,
    ("家", "便利店"): 12,
    ("家", "咖啡馆"): 18,
    ("家", "公园"): 15,
    ("家", "学校"): 20,
}

# (名称, 地点, 前置步骤, 总分钟, 精力消耗, 兴趣)
TEMPLATES = [
    ("写东西", "书房", ["翻出本子", "写下开头", "补一段细节"], 55, 0.10, "写作"),
    ("画画", "书房", ["铺开画纸", "勾线", "上色"], 70, 0.12, "画画"),
    ("做书签", "书房", ["挑纸", "画样式", "裁切", "压平"], 50, 0.08, "手作"),
    ("整理表情", "书房", ["分类", "重命名", "归档"], 40, 0.06, "整理"),
    ("听歌发呆", "卧室", ["戴上耳机", "发呆"], 35, 0.02, "音乐"),
    ("窝着看番", "客厅", ["打开番剧", "看一集"], 50, 0.03, "看番"),
    ("泡茶", "厨房", ["烧水", "泡茶"], 15, 0.02, "生活"),
    ("整理房间", "卧室", ["收衣服", "擦桌子", "归位"], 35, 0.08, "生活"),
    ("去便利店", "便利店", ["出门", "挑东西", "结账"], 35, 0.05, "生活"),
    ("在咖啡馆坐一会儿", "咖啡馆", ["点单", "找窗边座位", "发呆"], 45, 0.02, "生活"),
    ("去公园散步", "公园", ["出门", "沿路走一圈", "找长椅坐"], 45, 0.06, "散步"),
    ("在食堂吃饭", "学校", ["排队", "吃饭"], 30, 0.03, "生活"),
]

SLEEP_HOURS = {0, 1, 2, 3, 4, 5, 6}




class LifeState:
    """持久化的地点/活动/物品/计划状态。"""

    def __init__(self, path: Path, config, world=None):
        self.path = Path(path)
        self.config = config
        self.world = world
        self._data = self._load()
        if self.world is not None:
            node = self.world.node_id_by_name(str(self._data.get("location") or "卧室"))
            if node:
                self.world.set_current(node)
        self._advance()
        self._save()

    @staticmethod
    def _default_dimensions() -> dict:
        return {
            "joy": 0.42, "calm": 0.62, "sadness": 0.08, "anger": 0.04,
            "anxiety": 0.18, "loneliness": 0.25, "confidence": 0.52,
            "curiosity": 0.58, "focus": 0.55, "fatigue": 0.32,
            "hunger": 0.30, "thirst": 0.22, "sleepiness": 0.25,
            "social_battery": 0.62, "sensory_load": 0.22,
            "pain": 0.02, "comfort": 0.66, "motivation": 0.55,
            "creativity": 0.58, "playfulness": 0.46, "tenderness": 0.45,
            "irritability": 0.08, "patience": 0.62, "trust": 0.42,
            "nostalgia": 0.18, "boredom": 0.28, "stress": 0.24,
            "satisfaction": 0.48, "warmth": 0.50, "alertness": 0.58,
            "appetite": 0.42, "restlessness": 0.22, "shyness": 0.38,
            "gratitude": 0.30, "hope": 0.46,
        }

    # ---------- persistence ----------
    def _default(self) -> dict:
        now = _now()
        hour = _hour(now)
        activity = None
        if hour in SLEEP_HOURS:
            activity = self._make_activity("睡觉", "卧室", ["洗漱", "躺下"], 8 * 60, 0.0, "休息", phase="active")
        return {
            "version": 2,
            "updated": now,
            "location": "卧室",
            "location_since": now,
            "travel": {},
            "energy": 0.68,
            "activity": activity,
            "idle_until": now,
            "plans": [],
            "history": [],
            "share_pool": [],
            "last_share": 0,
            "share_stats": {},
            "pending_shares": [],
            "tool_runs": [],
            "dimensions": {
                "joy": 0.42, "calm": 0.62, "sadness": 0.08, "anger": 0.04,
                "anxiety": 0.18, "loneliness": 0.25, "confidence": 0.52,
                "curiosity": 0.58, "focus": 0.55, "fatigue": 0.32,
                "hunger": 0.30, "thirst": 0.22, "sleepiness": 0.25,
                "social_battery": 0.62, "sensory_load": 0.22,
                "pain": 0.02, "comfort": 0.66, "motivation": 0.55,
                "creativity": 0.58, "playfulness": 0.46, "tenderness": 0.45,
                "irritability": 0.08, "patience": 0.62, "trust": 0.42,
                "nostalgia": 0.18, "boredom": 0.28, "stress": 0.24,
                "satisfaction": 0.48, "warmth": 0.50, "alertness": 0.58,
                "appetite": 0.42, "restlessness": 0.22, "shyness": 0.38,
                "gratitude": 0.30, "hope": 0.46,
            },
            "preferences": {
                "aesthetic": ["清爽", "安静", "手作"],
                "interests": {"手作": 0.62, "写作": 0.56, "画画": 0.58, "音乐": 0.52, "散步": 0.5},
                "boundaries": ["不编造没有发生的经历", "不把工具未执行的结果说成完成", "不替别人回答"],
                "updated": now,
            },
        }

    def _load(self) -> dict:
        data = self._default()
        if not self.path.exists():
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return data
        except Exception as error:
            logger.warning(f"[life] 读取状态失败，使用默认值：{error}")
            return data
        for key in ("location", "location_since", "travel", "activity", "idle_until",
                    "plans", "history", "share_pool", "last_share", "share_stats",
                    "pending_shares", "tool_runs", "dimensions", "preferences"):
            if key in raw:
                data[key] = raw[key]
        try:
            data["energy"] = _clamp(float(raw.get("energy", data["energy"])))
        except (TypeError, ValueError):
            data["energy"] = data["energy"]
        data["location"] = str(data.get("location") or "卧室")
        if data["location"] not in LOCATIONS:
            data["location"] = "卧室"
        # 离线补算上限：只推进当前活动，不批量模拟未发生的经历
        try:
            elapsed = max(0, _now() - int(raw.get("updated") or _now()))
        except (TypeError, ValueError):
            elapsed = 0
        if elapsed > 6 * 3600:
            activity = data.get("activity")
            if isinstance(activity, dict) and activity.get("name") != "睡觉":
                # 跨天/长时间离线：保留未完成活动，暂停而不是抹掉，也不批量补算
                activity["phase"] = "paused"
                activity["remaining_minutes"] = max(10, int(activity.get("remaining_minutes") or 25))
                activity["offline_paused"] = True
                activity["resume_at"] = _now() + 10 * 60
            else:
                data["activity"] = None
            data["travel"] = {}
            if not isinstance(data.get("activity"), dict):
                data["location"] = "卧室"
            data["location_since"] = _now()
            data["idle_until"] = _now()
            data["updated"] = _now()
        else:
            data["updated"] = int(raw.get("updated") or _now())
        if not isinstance(data.get("preferences"), dict):
            data["preferences"] = self._default()["preferences"]
        data["history"] = data.get("history") if isinstance(data.get("history"), list) else []
        data["share_pool"] = data.get("share_pool") if isinstance(data.get("share_pool"), list) else []
        data["share_stats"] = data.get("share_stats") if isinstance(data.get("share_stats"), dict) else {}
        data["pending_shares"] = data.get("pending_shares") if isinstance(data.get("pending_shares"), list) else []
        data["tool_runs"] = data.get("tool_runs") if isinstance(data.get("tool_runs"), list) else []
        dims = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}
        defaults = self._default_dimensions()
        data["dimensions"] = {k: _clamp(float(dims.get(k, v))) for k, v in defaults.items()}
        return data

    def _save(self) -> None:
        self._data["updated"] = _now()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            logger.warning(f"[life] 保存状态失败：{error}")

    # ---------- activity helpers ----------
    def _make_activity(self, name: str, location: str, steps: list[str], minutes: int,
                       energy_cost: float, interest: str, *, phase: str = "prepare") -> dict:
        now = _now()
        return {
            "id": f"{int(now)}-{random.randint(100, 999)}",
            "name": name,
            "location": location,
            "kind": LOCATIONS.get(location, {}).get("kind", "home"),
            "interest": interest,
            "steps": steps,
            "step": steps[0] if steps else "",
            "phase": phase,
            "started": now,
            "planned_end": now + max(1, minutes) * 60,
            "progress": 0.0,
            "energy_cost": energy_cost,
            "items": [],
            "tool_result": False,
            "result_note": "",
            "simulated": True,
        }

    def _start(self, name: str, location: str, steps: list[str], minutes: int,
               energy_cost: float, interest: str) -> dict:
        activity = self._make_activity(name, location, steps, minutes, energy_cost, interest)
        activity["items"] = [f"{name}相关材料"]
        self._data["activity"] = activity
        self._data["location"] = location
        self._data["location_since"] = _now()
        if self.world is not None:
            node = self.world.node_id_by_name(location)
            if node:
                self.world.set_current(node)
        plans = [p for p in (self._data.get("plans") or []) if p.get("activity") != name]
        plans.append({"title": f"完成{name}", "activity": name, "created": _now()})
        self._data["plans"] = plans[-8:]
        return activity

    def start_phone_play(self, plan: str, minutes: int) -> dict:
        """把获准的电脑时间写成连续生活活动，而不是游离在生活状态之外。"""
        plan = str(plan or "玩一会儿电脑")[:500]
        minutes = max(5, min(240, int(minutes or 20)))
        now = _now()
        current = self._data.get("activity")
        if isinstance(current, dict) and current.get("name") in ("使用电脑", "玩手机"):
            current["planned_end"] = now + minutes * 60
            current["phone_plan"] = plan
            self._data["updated"] = now
            self._save()
            return self.snapshot()
        # 保留原来活动的剩余时间，电脑结束后可手动/自然恢复；当前先进入使用电脑。
        if isinstance(current, dict) and current.get("phase") in ("prepare", "active"):
            current["phase"] = "paused"
            current["remaining_minutes"] = max(5, int((int(current.get("planned_end") or now) - now) / 60))
            self._data["paused_activity"] = dict(current)
            self._data.setdefault("plans", []).append({
                "title": f"继续{current.get('name')}", "activity": current.get("name"),
                "created": now, "paused_for_phone": True,
            })
            self._data["plans"] = self._data["plans"][-8:]
        activity = self._make_activity("使用电脑", self._data.get("location") or "卧室",
                                       ["打开电脑", "刷一会儿"], minutes, 0.02, "电脑", phase="active")
        activity["phone_play"] = True
        activity["phone_plan"] = plan
        activity["tool_result"] = True
        activity["items"] = ["电脑"]
        self._data["activity"] = activity
        self._data["updated"] = now
        self._data["idle_until"] = 0
        self._save()
        return self.snapshot()

    def end_phone_play(self, reason: str = "phone_time") -> dict:
        activity = self._data.get("activity")
        if isinstance(activity, dict) and activity.get("name") in ("使用电脑", "玩手机"):
            self._complete(activity, reason=reason)
            paused = self._data.pop("paused_activity", None)
            if isinstance(paused, dict):
                paused["phase"] = "active"
                paused["planned_end"] = _now() + max(5, int(paused.get("remaining_minutes") or 25)) * 60
                paused.pop("remaining_minutes", None)
                self._data["activity"] = paused
                self._data["idle_until"] = 0
            else:
                # 玩完电脑不要立刻进入长时间空转，也不要把“刷电脑”当成唯一后续活动。
                self._data["idle_until"] = 0
                location = self._data.get("location") or "卧室"
                candidates = [t for t in TEMPLATES
                              if t[1] == location and t[0] not in ("刷电脑", "使用电脑")]
                if not candidates:
                    candidates = [t for t in TEMPLATES
                                  if t[1] in ("卧室", "客厅", "书房") and t[0] not in ("刷电脑", "使用电脑")]
                if candidates and _hour() not in SLEEP_HOURS:
                    name, place, steps, minutes, cost, interest = random.choice(candidates)
                    self._start(name, place, steps, minutes, cost, interest)
                else:
                    self._data["idle_until"] = _now() + random.randint(180, 480)
            self._save()
        return self.snapshot()

    def _complete(self, activity: dict, *, reason: str = "natural") -> None:
        activity["phase"] = "complete"
        activity["progress"] = 1.0
        activity["ended"] = _now()
        activity["result_note"] = reason
        self._data["activity"] = None
        self._data["energy"] = _clamp(self._data["energy"] - float(activity.get("energy_cost", 0)))
        self._data["idle_until"] = _now() + random.randint(25, 65) * 60
        self._data["history"] = (self._data.get("history") or [])[-99:] + [{
            "time": _now(), "name": activity.get("name"), "location": activity.get("location"),
            "phase": "complete", "progress": 1.0, "tool_result": activity.get("tool_result", False),
        }]
        # 低频突发事件：不是每次都有，触发后把事件写入分享池并应用后果
        if self.world is not None:
            try:
                event = self.world.maybe_event(str(activity.get("name") or ""), str(activity.get("location") or ""))
            except Exception:
                event = None
            if event:
                self._data["activity"] = None
                self._data["energy"] = _clamp(self._data["energy"] + float(event.get("energy_delta") or 0))
                if event.get("cancel_remaining"):
                    self._data["idle_until"] = _now() + max(20, int(event.get("duration_minutes") or 60)) * 60
                    if self.world is not None:
                        self.world.set_current("bedroom")
                        self._data["location"] = "卧室"
                pool = self._data.get("share_pool") or []
                pool.append({
                    "id": f"{_now()}-{random.randint(100, 999)}",
                    "dedupe": f"surprise:{event.get('kind')}:{_now()}",
                    "kind": "surprise",
                    "summary": str(event.get("summary") or ""),
                    "share_hint": str(event.get("share_hint") or ""),
                    "detail": str(event.get("detail") or ""),
                    "simulated": True, "tool_result": False,
                    "time": _now(), "shared": False,
                })
                self._data["share_pool"] = pool[-20:]
                self._save()
                return
        # 只有产生了具体进展才进入待分享池；睡觉、短活动不进入
        if activity.get("name") == "睡觉" or float(activity.get("progress") or 0) < 0.35:
            self._save()
            return
        key = f"{(activity.get('name') or '').strip()}:{datetime.fromtimestamp(_now() + TZ_OFFSET * 3600, timezone.utc).strftime('%Y-%m-%d')}"
        pool = self._data.get("share_pool") or []
        if not any(item.get("dedupe") == key for item in pool):
            name = str(activity.get("name") or "")
            if name in {"刷手机", "窝着看番", "听歌发呆", "去咖啡馆坐一会儿"}:
                kind = "media_thought"
                summary = "刚刷到/看到一段有点意思的内容，脑子里还留着一个观点"
                share_hint = (
                    "你刚刷完视频/番/帖子，想把一个你觉得有道理、猎奇、有趣或有点抽象的"
                    "观点搬给群友；不要声称是权威新闻，不要编造具体链接或数据，"
                    "用你自己的判断说一两句，可以有一点争议但不要攻击或低俗。"
                )
            elif name in {"写东西", "画画", "做书签", "整理表情"}:
                kind = "work_fragment"
                summary = f"{name}做到一半以上，刚收尾了一点"
                share_hint = (
                    "你刚做了一段时间自己的小东西，想随口分享一点过程或感受；"
                    "如果没有真实工具结果，不要声称已经生成可交付作品或真正完成。"
                )
            else:
                kind = "activity_complete"
                summary = f"{name}做到一半以上，刚刚收尾"
                share_hint = "分享一件刚收尾的小事，不要把它说成重大经历。"
            if self._share_kind_allowed(kind):
                pool.append({
                    "id": f"{_now()}-{random.randint(100, 999)}",
                    "dedupe": key,
                    "kind": kind,
                    "summary": summary,
                    "share_hint": share_hint,
                    "detail": f"在{activity.get('location')}完成{activity.get('name')}；进度 100%",
                    "simulated": not bool(activity.get("tool_result")),
                    "tool_result": bool(activity.get("tool_result")),
                    "time": _now(),
                    "shared": False,
                })
            self._data["share_pool"] = pool[-20:]
        self._data["plans"] = [
            p for p in (self._data.get("plans") or [])
            if p.get("activity") != activity.get("name")
        ][-8:]
        # 偏好慢慢变化：同一天最多几次，单次幅度很小
        try:
            self.reinforce_preference(str(activity.get("interest") or ""), 0.008)
        except Exception:
            pass

    def _choose_template(self) -> tuple | None:
        hour = _hour()
        if hour in SLEEP_HOURS:
            return ("睡觉", "卧室", ["洗漱", "躺下"], 8 * 60, 0.0, "休息")
        location = self._data.get("location") or "卧室"
        weather = self.world.weather() if self.world is not None else {}
        if weather.get("condition") in ("rain", "storm") and random.random() < 0.8:
            location = random.choice(["卧室", "客厅", "书房"])
        candidates = [item for item in TEMPLATES if item[1] == location]
        if not candidates:
            candidates = [item for item in TEMPLATES if LOCATIONS.get(item[1], {}).get("kind") == "home"]
        if not candidates:
            candidates = list(TEMPLATES[:4])
        prefs = (self._data.get("preferences") or {}).get("interests") or {}
        # 稳定偏好只做温和加权，不决定一切
        weighted = []
        for item in candidates:
            weight = 1.0 + float(prefs.get(item[5], 0.5)) * 0.8
            weighted.append((weight, item))
        total = sum(weight for weight, _ in weighted)
        pick = random.random() * total
        cur = 0.0
        for weight, item in weighted:
            cur += weight
            if pick <= cur:
                return item
        return candidates[-1]

    def _advance(self, now: int | None = None) -> None:
        now = now or _now()
        travel = self._data.get("travel") or {}
        if travel:
            try:
                arrived = int(travel.get("arrive") or 0)
            except (TypeError, ValueError):
                arrived = 0
            if arrived and now >= arrived:
                self._data["location"] = str(travel.get("to") or "卧室")
                self._data["location_since"] = now
                self._data["travel"] = {}
                if self.world is not None:
                    node = self.world.node_id_by_name(self._data["location"])
                    if node:
                        self.world.set_current(node)
                activity = self._data.get("activity")
                if isinstance(activity, dict) and activity.get("phase") == "paused":
                    activity["phase"] = "active"
                    activity["planned_end"] = now + max(10, int(activity.get("remaining_minutes", 30))) * 60
            self._data["updated"] = now
            return
        activity = self._data.get("activity")
        elapsed = max(0, now - int(self._data.get("updated") or now))
        self._drift_dimensions(elapsed, activity)
        if isinstance(activity, dict):
            phase = str(activity.get("phase") or "active")
            if activity.get("name") == "睡觉" and phase == "active":
                self._data["energy"] = _clamp(self._data["energy"] + elapsed / 3600 * 0.09)
            if phase == "prepare" and now >= int(activity.get("started") or now) + 5 * 60:
                activity["phase"] = "active"
            if phase == "paused" and int(activity.get("resume_at") or 0) and now >= int(activity.get("resume_at") or 0):
                activity["phase"] = "active"
                activity["planned_end"] = now + max(10, int(activity.get("remaining_minutes") or 25)) * 60
                activity.pop("resume_at", None)
                phase = "active"
            if phase == "active":
                start = int(activity.get("started") or now)
                end = int(activity.get("planned_end") or now)
                span = max(60, end - start)
                activity["progress"] = round(_clamp((now - start) / span), 3)
                steps = activity.get("steps") or []
                if steps:
                    activity["step"] = steps[min(len(steps) - 1, int(activity["progress"] * len(steps)))]
                if now >= end:
                    self._complete(activity)
                elif self._data["energy"] <= 0.16:
                    activity["phase"] = "paused"
                    activity["remaining_minutes"] = max(5, int((end - now) / 60))
            if self._data["energy"] <= 0.12 and str((self._data.get("activity") or {}).get("phase")) == "active":
                self._data["activity"]["phase"] = "paused"
                self._data["activity"]["remaining_minutes"] = 15
            self._data["updated"] = now
            return
        if now < int(self._data.get("idle_until") or 0):
            self._data["energy"] = _clamp(self._data["energy"] + elapsed / 3600 * 0.02)
            self._data["updated"] = now
            return
        hour = _hour(now)
        if hour in SLEEP_HOURS and self._data.get("location") != "卧室":
            self.travel_to("卧室")
            return
        item = self._choose_template()
        if not item:
            return
        name, location, steps, minutes, cost, interest = item
        if location != self._data.get("location"):
            # 空闲时偶尔出门；不瞬移，先进入途中状态
            if random.random() < 0.35:
                self.travel_to(location)
                return
            location = self._data.get("location") or "卧室"
            candidate = next((x for x in TEMPLATES if x[1] == location), None)
            if candidate is None:
                candidate = next((x for x in TEMPLATES if x[1] in ("卧室", "客厅", "书房")), TEMPLATES[0])
            name, location, steps, minutes, cost, interest = candidate
        self._start(name, location, steps, minutes, cost, interest)
        self._data["updated"] = now

    def _drift_dimensions(self, elapsed: int, activity: dict | None) -> None:
        if elapsed <= 0:
            return
        dims = self._data.setdefault("dimensions", self._default_dimensions())
        minutes = min(12 * 60, elapsed) / 60.0
        sleeping = isinstance(activity, dict) and activity.get("name") == "睡觉"
        if sleeping:
            dims["fatigue"] = _clamp(dims["fatigue"] - 0.05 * minutes)
            dims["sleepiness"] = _clamp(dims["sleepiness"] - 0.06 * minutes)
            dims["alertness"] = _clamp(dims["alertness"] - 0.02 * minutes)
        else:
            dims["fatigue"] = _clamp(dims["fatigue"] + 0.008 * minutes)
            dims["sleepiness"] = _clamp(dims["sleepiness"] + 0.004 * minutes)
            dims["alertness"] = _clamp(dims["alertness"] - 0.003 * minutes)
        dims["hunger"] = _clamp(dims["hunger"] + 0.006 * minutes)
        dims["thirst"] = _clamp(dims["thirst"] + 0.008 * minutes)
        dims["stress"] = _clamp(dims["stress"] + 0.0015 * minutes)
        dims["comfort"] = _clamp(dims["comfort"] - 0.001 * minutes)

    def nudge_dimensions(self, delta: dict, *, amount: float = 1.0) -> dict:
        dims = self._data.setdefault("dimensions", self._default_dimensions())
        for key, value in (delta or {}).items():
            if key in dims:
                try:
                    dims[key] = _clamp(float(dims[key]) + float(value) * amount)
                except (TypeError, ValueError):
                    continue
        self._save()
        return dict(dims)

    # ---------- public API ----------
    def snapshot(self) -> dict:
        self._advance()
        activity = self._data.get("activity")
        self._save()
        snap = {
            "location": self._data.get("location") or "卧室",
            "location_kind": LOCATIONS.get(self._data.get("location") or "", {}).get("kind", "home"),
            "location_since": int(self._data.get("location_since") or 0),
            "travel": dict(self._data.get("travel") or {}),
            "energy": round(float(self._data.get("energy") or 0), 3),
            "activity": dict(activity) if isinstance(activity, dict) else None,
            "plans": list(self._data.get("plans") or [])[-8:],
            "history": list(self._data.get("history") or [])[-8:],
            "share_pool": [dict(x) for x in (self._data.get("share_pool") or []) if not x.get("shared")][-5:],
            "share_stats": dict(self._data.get("share_stats") or {}),
            "tool_runs": list(self._data.get("tool_runs") or [])[-10:],
            "dimensions": dict(self._data.get("dimensions") or {}),
            "preferences": dict(self._data.get("preferences") or {}),
            "world": self.world.snapshot() if self.world is not None else {},
            "updated": int(self._data.get("updated") or _now()),
        }
        return snap

    def prompt_block(self) -> str:
        snap = self.snapshot()
        lines = [
            f"【你的连续生活状态】你此刻在「{snap['location']}」。"
        ]
        travel = snap.get("travel") or {}
        if travel:
            lines.append(f"你正在从{travel.get('from')}前往{travel.get('to')}的路上，还没到。")
        activity = snap.get("activity")
        if isinstance(activity, dict):
            phase = activity.get("phase")
            phase_text = {"prepare": "准备", "active": "进行中", "paused": "暂停", "complete": "完成", "abandoned": "放弃"}.get(phase, phase)
            lines.append(
                f"当前活动「{activity.get('name')}」阶段：{phase_text}，进度约 {round(float(activity.get('progress') or 0) * 100)}%，"
                f"正在做：{activity.get('step') or '—'}。"
            )
            if activity.get("phone_play"):
                lines.append("这是你获准使用手机的时间；可以按手机任务行动，不要虚构没看到的内容。")
            elif activity.get("tool_result"):
                lines.append("这项活动有真实工具结果，可以按结果描述完成情况。")
            else:
                lines.append(
                    "这项活动是模拟生活事件，没有真实工具结果；不要声称已经生成作品、查过资料或真正完成。"
                )
            if activity.get("items"):
                lines.append("相关物品：" + "、".join(str(x) for x in activity.get("items")[:6]) + "。")
        else:
            lines.append("你现在处于空闲，没有正在进行的活动。")
        prefs = snap.get("preferences") or {}
        aesthetic = prefs.get("aesthetic") or []
        if aesthetic:
            lines.append(
                "你的稳定偏好是：" + "、".join(str(x) for x in aesthetic[:4])
                + "。这会影响你怎么选、怎么评价，但不需要每次都说出来，也不要为了迎合对方立刻改口。"
            )
        plans = snap.get("plans") or []
        if plans:
            lines.append("后续计划：" + "；".join(str(p.get("title") or p.get("activity") or "") for p in plans[:3]) + "。")
        dims = snap.get("dimensions") or {}
        highlights = []
        labels = {"anger": "生气", "fatigue": "疲惫", "anxiety": "焦虑", "joy": "开心",
                  "loneliness": "孤单", "stress": "压力", "curiosity": "好奇", "sadness": "低落"}
        for key, high in (("anger", .55), ("fatigue", .68), ("anxiety", .6), ("joy", .7),
                          ("loneliness", .65), ("stress", .62), ("curiosity", .7), ("sadness", .55)):
            if float(dims.get(key) or 0) >= high:
                highlights.append(labels.get(key, key))
        if highlights:
            lines.append("此刻比较明显的状态：" + "、".join(highlights) + "。只用语气自然带出，不要报数值。")
        if self.world is not None:
            try:
                lines.append(self.world.prompt_block())
            except Exception:
                pass
        lines.append(
            "聊天不会替换当前活动；短暂聊天后继续原来的事。不要主动播报这套状态，除非对方问起。"
        )
        return "\n".join(lines)

    def travel_to(self, destination: str, reason: str = "") -> dict:
        destination = str(destination or "").strip()
        if destination not in LOCATIONS:
            return {"ok": False, "error": "unknown_location"}
        current = self._data.get("location") or "卧室"
        if destination == current:
            return {"ok": False, "error": "already_there"}
        minutes = TRAVEL_MINUTES.get((current, destination))
        route_desc = ""
        route_nodes = []
        if self.world is not None:
            start = self.world.node_id_by_name(current)
            target = self.world.node_id_by_name(destination) or self.world.ensure_destination(destination)
            options = self.world.plan_routes(start, target) if start and target else []
            if options:
                chosen = random.choice(options)
                minutes = max(1, int(chosen.get("minutes") or 1))
                route_desc = str(chosen.get("description") or "")
                route_nodes = chosen.get("nodes") or []
        if minutes is None:
            minutes = 10 if LOCATIONS.get(current, {}).get("kind") == "home" and LOCATIONS.get(destination, {}).get("kind") == "home" else 20
        now = _now()
        activity = self._data.get("activity")
        if isinstance(activity, dict) and activity.get("phase") in ("prepare", "active"):
            activity["phase"] = "paused"
            activity["remaining_minutes"] = max(5, int((int(activity.get("planned_end") or now) - now) / 60))
        self._data["travel"] = {
            "from": current, "to": destination, "start": now,
            "arrive": now + minutes * 60, "minutes": minutes, "reason": reason,
            "route": route_desc, "route_nodes": route_nodes,
        }
        self._save()
        return {"ok": True, "from": current, "to": destination, "minutes": minutes}

    def apply_action(self, action: str, **kwargs) -> dict:
        action = str(action or "").strip()
        if action == "travel":
            return self.travel_to(kwargs.get("location", ""), kwargs.get("reason", ""))
        if action == "pause":
            activity = self._data.get("activity")
            if isinstance(activity, dict) and activity.get("phase") == "active":
                activity["phase"] = "paused"
                activity["remaining_minutes"] = max(5, int((int(activity.get("planned_end") or _now()) - _now()) / 60))
                self._save()
                return {"ok": True}
            return {"ok": False, "error": "no_active_activity"}
        if action == "resume":
            activity = self._data.get("activity")
            if isinstance(activity, dict) and activity.get("phase") == "paused":
                activity["phase"] = "active"
                activity["planned_end"] = _now() + max(5, int(activity.get("remaining_minutes", 20))) * 60
                self._save()
                return {"ok": True}
            return {"ok": False, "error": "not_paused"}
        if action == "abandon":
            activity = self._data.get("activity")
            if isinstance(activity, dict):
                activity["phase"] = "abandoned"
                self._data["activity"] = None
                self._data["idle_until"] = _now() + 15 * 60
                self._save()
                return {"ok": True}
            return {"ok": False, "error": "no_activity"}
        if action == "complete":
            activity = self._data.get("activity")
            if isinstance(activity, dict):
                self._complete(activity, reason="manual")
                self._save()
                return {"ok": True}
            return {"ok": False, "error": "no_activity"}
        if action == "wake":
            # 只结束“手机后长时间空转”，让生活状态立即选一个非手机活动。
            self._data["idle_until"] = 0
            self._advance()
            self._save()
            return {"ok": True, "activity": (self._data.get("activity") or {}).get("name")}
        return {"ok": False, "error": "unknown_action"}

    def record_tool_run(self, kind: str, success: bool, note: str = "") -> dict:
        run = {"kind": str(kind or "tool"), "success": bool(success), "note": str(note or "")[:200], "time": _now()}
        self._data["tool_runs"] = ((self._data.get("tool_runs") or []) + [run])[-100:]
        self._save()
        return run

    def record_tool_result(self, success: bool, note: str = "") -> dict:
        activity = self._data.get("activity")
        if not isinstance(activity, dict):
            return {"ok": False, "error": "no_activity"}
        activity["tool_result"] = bool(success)
        activity["simulated"] = not bool(success)
        activity["result_note"] = str(note or "")[:200]
        self._save()
        return {"ok": True, "tool_result": bool(success)}

    def add_thought_event(self, seed: str = "") -> dict:
        """群安静时也可以从一个无害的抽象想法开始，而不是只能分享生活。"""
        seed = str(seed or "").strip()
        if not seed:
            prefs = (self._data.get("preferences") or {}).get("aesthetic") or ["安静", "清爽", "手作"]
            seed = random.choice([
                "如果把所有日常小事都做成一张小卡片，会不会比日记更容易坚持",
                "为什么有些声音听久了会让人想起一个没去过的地方",
                "把喜欢的东西留白一点，会不会反而更耐看",
            ])
        event = {
            "id": f"{_now()}-{random.randint(100, 999)}",
            "dedupe": f"thought:{_now()}",
            "kind": "idea",
            "summary": seed,
            "share_hint": (
                "你突然想到一个有点逆天/抽象但无害的小观点，想随口扔到群里；"
                "可以有一点争议，但不要攻击任何人、不要低俗、不要假装它是事实或新闻。"
            ),
            "detail": seed,
            "simulated": True,
            "tool_result": False,
            "time": _now(),
            "shared": False,
        }
        self._data["share_pool"] = ((self._data.get("share_pool") or []) + [event])[-20:]
        self._save()
        return event

    def settle_shares(self) -> None:
        now = _now()
        pending = self._data.get("pending_shares") or []
        keep = []
        changed = False
        for item in pending:
            if now - int(item.get("time") or 0) > 15 * 60:
                kind = str(item.get("kind") or "other")
                stats = self._data.setdefault("share_stats", {}).setdefault(
                    kind, {"sent": 0, "replied": 0, "ignored": 0, "negative": 0}
                )
                stats["ignored"] = int(stats.get("ignored") or 0) + 1
                changed = True
            else:
                keep.append(item)
        self._data["pending_shares"] = keep[-20:]
        if changed:
            self._save()

    def pending_share_events(self, gid):
        self.settle_shares()
        return [dict(r) for r in self._data.get('pending_shares', []) if str(r.get('gid')) == str(gid)]

    def note_group_reply(self, gid: str, text: str, *, event_id=None, sentiment=None) -> bool:
        self.settle_shares()
        if not event_id or sentiment not in ('positive', 'neutral', 'negative'):
            return False
        changed = False
        now = _now()
        pending = self._data.get("pending_shares") or []
        keep = []
        for item in pending:
            if (not changed and str(item.get("gid")) == str(gid) and item.get("event_id") == event_id
                    and now - int(item.get("time") or 0) <= 15 * 60):
                kind = str(item.get("kind") or "other")
                stats = self._data.setdefault("share_stats", {}).setdefault(
                    kind, {"sent": 0, "replied": 0, "ignored": 0, "negative": 0}
                )
                if sentiment == 'negative':
                    stats["negative"] = int(stats.get("negative") or 0) + 1
                else:
                    stats["replied"] = int(stats.get("replied") or 0) + 1
                changed = True
            else:
                keep.append(item)
        self._data["pending_shares"] = keep[-20:]
        if changed:
            self._save()
        return changed

    def _share_kind_allowed(self, kind: str) -> bool:
        """长期没人回应的分享类别不再反复占用主动发言。

        只看反馈计数，不看标题/关键词：某个类别已发过至少两次、一次回应都没有、
        且被忽略至少两次时，默认不再从该类别取事件。有人回应过就恢复权重。
        """
        stats = (self._data.get("share_stats") or {}).get(str(kind or "other")) or {}
        sent = int(stats.get("sent") or 0)
        replied = int(stats.get("replied") or 0)
        ignored = int(stats.get("ignored") or 0)
        return not (sent >= 2 and replied == 0 and ignored >= 2)

    def choose_share_event(self) -> dict | None:
        self.settle_shares()
        pool = [x for x in (self._data.get("share_pool") or []) if not x.get("shared")]
        if not pool:
            return None
        stats = self._data.get("share_stats") or {}
        weighted = []
        for event in pool:
            kind = str(event.get("kind") or "other")
            if not self._share_kind_allowed(kind):
                continue
            s = stats.get(kind) or {}
            weight = 1.0 + 0.35 * int(s.get("replied") or 0) - 0.2 * int(s.get("ignored") or 0) - 0.6 * int(s.get("negative") or 0)
            weighted.append((max(0.15, min(3.0, weight)), event))
        if not weighted:
            return None
        total = sum(w for w, _ in weighted)
        pick = random.random() * total
        cursor = 0.0
        for weight, event in weighted:
            cursor += weight
            if pick <= cursor:
                return event
        return weighted[-1][1]

    def mark_share_sent(self, event: dict, gid: str, message_id: int | None = None) -> None:
        eid = str(event.get("id") or "")
        for item in self._data.get("share_pool") or []:
            if str(item.get("id")) == eid:
                item["shared"] = True
                break
        kind = str(event.get("kind") or "other")
        stats = self._data.setdefault("share_stats", {}).setdefault(
            kind, {"sent": 0, "replied": 0, "ignored": 0, "negative": 0}
        )
        stats["sent"] = int(stats.get("sent") or 0) + 1
        self._data.setdefault("pending_shares", []).append({
            "id": f"{_now()}-{random.randint(100, 999)}",
            "event_id": eid, "kind": kind, "gid": str(gid),
            "message_id": message_id, "time": _now(),
        })
        self._data["pending_shares"] = (self._data.get("pending_shares") or [])[-20:]
        self._save()

    def take_share_event(self) -> dict | None:
        pool = self._data.get("share_pool") or []
        for item in pool:
            if not item.get("shared"):
                item["shared"] = True
                self._data["last_share"] = _now()
                self._save()
                return dict(item)
        return None

    def reinforce_preference(self, key: str, delta: float = 0.01) -> dict:
        """少量、带日上限的偏好变化；一句夸奖不会让立场突变。"""
        prefs = self._data.setdefault("preferences", {})
        interests = prefs.setdefault("interests", {})
        key = str(key or "").strip()
        if not key:
            return {"ok": False, "error": "empty"}
        today = datetime.fromtimestamp(_now() + TZ_OFFSET * 3600, timezone.utc).strftime("%Y-%m-%d")
        changed = prefs.setdefault("daily_changes", {})
        if changed.get("date") != today:
            changed.clear()
            changed["date"] = today
            changed["count"] = 0
        if int(changed.get("count") or 0) >= 3:
            return {"ok": False, "error": "daily_limit"}
        old = float(interests.get(key, 0.5))
        interests[key] = round(_clamp(old + max(-0.02, min(0.02, float(delta)))), 3)
        changed["count"] = int(changed.get("count") or 0) + 1
        prefs["updated"] = _now()
        self._save()
        return {"ok": True, "key": key, "value": interests[key]}

    def reset(self) -> None:
        self._data = self._default()
        self._save()

    def reload(self) -> None:
        self._data = self._load()
        self._advance()
        self._save()

    def flush(self) -> None:
        self._save()
