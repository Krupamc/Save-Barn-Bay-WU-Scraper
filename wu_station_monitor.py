#!/usr/bin/env python3
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("wu_station_state.json")
CONFIG_FILE = Path("stations.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


@dataclass
class StationResult:
    station_id: str
    name: str
    url: str
    status: str
    observed_text: Optional[str]
    checked_at: str
    alert_sent: bool = False


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> Dict[str, dict]:
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: Dict[str, dict]) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def fetch_station_status(url: str) -> dict:
    """Fetch station page and detect Online/Offline from span text, preferring Online."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    online_parent = None
    offline_parent = None

    for span in soup.find_all("span"):
        text = span.get_text(strip=True)
        if text == "Online" and online_parent is None:
            online_parent = span.parent
        elif text == "Offline" and offline_parent is None:
            offline_parent = span.parent

    status = "unknown"
    observed_text = None

    # Prefer Online if both appear somewhere
    if online_parent is not None:
        status = "online"
        observed_text = online_parent.get_text(" ", strip=True)
    elif offline_parent is not None:
        status = "offline"
        observed_text = offline_parent.get_text(" ", strip=True)

    return {"status": status, "observed_text": observed_text}


def check_stations(config: dict) -> List[StationResult]:
    state = load_state()
    results: List[StationResult] = []
    threshold = config.get("offline_checks_before_alert", 3)
    now = datetime.now(timezone.utc).isoformat()

    for station in config["stations"]:
        station_id = station["station_id"]
        entry = state.get(
            station_id,
            {"consecutive_offline": 0, "alert_sent": False, "last_status": "unknown"},
        )
        url = station.get("url") or f"https://www.wunderground.com/dashboard/pws/{station_id}"

        try:
            fetched = fetch_station_status(url)
            status = fetched["status"]
            observed_text = fetched["observed_text"]
        except Exception as exc:
            status = "error"
            observed_text = str(exc)

        # Debug logging
        print(f"[CHECK] {station['name']} ({station_id}) => status={status}, text={observed_text}")

        # State machine
        if status == "offline":
            entry["consecutive_offline"] = entry.get("consecutive_offline", 0) + 1
        elif status == "online":
            # Recovery handling
            if entry.get("alert_sent"):
                print(f"[RECOVERED] {station['name']} ({station_id}) back online at {now}")
            entry["consecutive_offline"] = 0
            entry["alert_sent"] = False
        else:
            # unknown/error: do not change counters
            pass

        # Trigger offline alert (print only)
        if status == "offline" and entry["consecutive_offline"] >= threshold and not entry.get("alert_sent"):
            print(
                f"[OFFLINE] {station['name']} ({station_id}) appears offline "
                f"(checks={entry['consecutive_offline']}) at {now}"
            )
            entry["alert_sent"] = True

        entry["last_status"] = status
        entry["last_checked"] = now
        state[station_id] = entry

        results.append(
            StationResult(
                station_id=station_id,
                name=station["name"],
                url=url,
                status=status,
                observed_text=observed_text,
                checked_at=now,
                alert_sent=entry.get("alert_sent", False),
            )
        )

        time.sleep(config.get("delay_seconds", 2))

    save_state(state)
    return results


def main() -> int:
    config = load_config(CONFIG_FILE)
    results = check_stations(config)
    print("\n=== SUMMARY ===")
    print(json.dumps([asdict(r) for r in results], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())