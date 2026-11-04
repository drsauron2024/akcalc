"""Download and pin Arknights CN game data from jsDelivr.

The upstream repo is Kengxxiao/ArknightsGameData (community extraction of
official game data). We pin a commit SHA so results are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "data" / "raw" / "gamedata"

REPO = "Kengxxiao/ArknightsGameData"
FILES = [
    "character_table.json",
    "skill_table.json",
    "enemy_handbook_table.json",
    "range_table.json",
]
UA = "akcalc-course-project/0.1 (contact: local student project)"
# cdn.jsdelivr.net is heavily throttled on this network; gcore is much faster.
CDN_HOSTS = ["gcore.jsdelivr.net", "testingcf.jsdelivr.net", "cdn.jsdelivr.net"]


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_bytes(urls: list[str]) -> bytes:
    last: Exception | None = None
    for url in urls:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return resp.read()
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"download failed: {urls}: {last}")


def main() -> int:
    commit = http_json(f"https://api.github.com/repos/{REPO}/commits/master")
    sha = commit["sha"]
    commit_date = commit["commit"]["committer"]["date"]
    out_dir = OUT_ROOT / sha[:12]
    out_dir.mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "repo": REPO,
        "commit": sha,
        "commit_date": commit_date,
        "server": "zh_CN",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }

    for name in FILES:
        urls = [
            f"https://{host}/gh/{REPO}@{sha}/zh_CN/gamedata/excel/{name}"
            for host in CDN_HOSTS
        ]
        print(f"downloading {name} ...", flush=True)
        data = http_bytes(urls)
        path = out_dir / name
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        meta["files"][name] = {"sha256": digest, "size": len(data), "urls": urls}
        print(f"  -> {len(data):,} bytes  sha256={digest[:16]}...")

    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nSaved to {out_dir}")
    print(f"Pinned commit: {sha} ({commit_date})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
