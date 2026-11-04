"""Download the wiki mechanism pages used by the formula cards.

Snapshots are kept locally so the report's citations can be checked even if the
wiki changes. The script is deliberately polite: one request at a time with a
short delay, and a browser-like User-Agent.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "wiki"

PAGES = {
    "wiki_gg_damage.html": "https://arknights.wiki.gg/wiki/Damage",
    "wiki_gg_damage_Physical.html": "https://arknights.wiki.gg/wiki/Damage/Physical",
    "wiki_gg_damage_Arts.html": "https://arknights.wiki.gg/wiki/Damage/Arts",
    "wiki_gg_damage_True.html": "https://arknights.wiki.gg/wiki/Damage/True",
    "wiki_gg_Attribute_Attack_interval.html": "https://arknights.wiki.gg/wiki/Attribute/Attack_interval",
    "wiki_gg_Damage_Reduction.html": "https://arknights.wiki.gg/wiki/Damage_Reduction",
    "wiki_gg_Attribute_ATK.html": "https://arknights.wiki.gg/wiki/Attribute/ATK",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, url in PAGES.items():
        print(f"fetching {name} ...", flush=True)
        data = fetch(url)
        (OUT / name).write_bytes(data)
        print(f"  -> {len(data):,} bytes")
        time.sleep(1.5)
    print(f"saved to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
