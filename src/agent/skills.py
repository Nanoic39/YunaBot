"""Small persistent skill notes learned from successful phone operations."""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / 'data' / 'agent' / 'skills'


class SkillStore:
    def __init__(self, path: Path = DIR):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        safe = ''.join(ch for ch in str(name) if ch.isalnum() or ch in '-_')[:80]
        return self.path / ((safe or 'skill') + '.json')

    def hint(self, name: str, limit: int = 3) -> str:
        try:
            data = json.loads(self._file(name).read_text(encoding='utf-8'))
            rows = data.get('successes') or []
            lines = []
            for row in rows[-limit:]:
                steps = ' → '.join(str(x) for x in (row.get('steps') or [])[-8:])
                lines.append(f"- {row.get('goal','')[:100]}：{steps}")
            return '\n'.join(lines)[:1800]
        except Exception:
            return ''

    def snapshot(self, limit: int = 50) -> list[dict]:
        rows = []
        for path in sorted(self.path.glob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            successes = data.get('successes') or []
            if not successes:
                continue
            last = successes[-1]
            rows.append({'name': data.get('name') or path.stem,
                         'successes': len(successes),
                         'last_goal': str(last.get('goal') or '')[:160],
                         'steps': last.get('steps') or [],
                         'time': last.get('time') or 0})
        rows.sort(key=lambda r: float(r.get('time') or 0), reverse=True)
        return rows[:max(1, min(100, int(limit)))]

    def record(self, name: str, goal: str, steps: list[str]) -> None:
        try:
            path = self._file(name)
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                data = {'name': name, 'successes': []}
            data.setdefault('successes', []).append(
                {'goal': str(goal or '')[:300], 'steps': [str(x)[:160] for x in steps[-30:]],
                 'time': time.time()})
            data['successes'] = data['successes'][-30:]
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(path)
            path.chmod(0o600)
        except Exception:
            pass


skill_store = SkillStore()
