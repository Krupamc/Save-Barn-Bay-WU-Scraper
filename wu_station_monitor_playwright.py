#!/usr/bin/env python3
import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import smtplib
import ssl
from email.message import EmailMessage

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

STATE_FILE = Path("wu_station_state.json")
CONFIG_FILE = Path("stations.json")


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


def send_email(subject: str, body: str, smtp_cfg: dict, recipients: List[str]) -> None:
    """Send an email notification if SMTP and recipients are configured."""
    if not smtp_cfg or not recipients:
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_cfg["from_email"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg.get("port", 587)) as server:
        server.starttls(context=context)
        server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.send_message(msg)


def fetch_station_status_playwright(page, url: str) -> dict:
    # 1) Navigate and wait for basic load (no networkidle)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # 2) Give the Angular app a moment to kick in
    page.wait_for_timeout(2000)

    status = "unknown"
    observed_text = None

    try:
        page.wait_for_timeout(500)

        spans = page.query_selector_all("span")
        online_text = None
        offline_text = None

        for span in spans:
            text = (span.inner_text() or "").strip()
            if text == "Online" and online_text is None:
                parent = span.evaluate_handle("el => el.parentElement")
                online_text = parent.evaluate("el => el.innerText") if parent else text
            elif text == "Offline" and offline_text is None:
                parent = span.evaluate_handle("el => el.parentElement")
                offline_text = parent.evaluate("el => el.innerText") if parent else text

        if online_text is not None:
            status = "online"
            observed_text = " ".join(online_text.split())
        elif offline_text is not None:
            status = "offline"
            observed_text = " ".join(offline_text.split())

    except PlaywrightTimeoutError:
        status = "unknown"
        observed_text = "Timed out waiting for Online/Offline span"

    return {"status": status, "observed_text": observed_text}


def check_stations(config: dict) -> List[StationResult]:
    state = load_state()
    results: List[StationResult] = []
    threshold = config.get("offline_checks_before_alert", 3)
    now = datetime.now(timezone.utc).isoformat()

    smtp_cfg = config.get("smtp", {})
    global_recipients = config.get("default_recipients", [])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for station in config["stations"]:
            station_id = station["station_id"]
            entry = state.get(
                station_id,
                {"consecutive_offline": 0, "alert_sent": False, "last_status": "unknown"},
            )
            url = station.get("url") or f"https://www.wunderground.com/dashboard/pws/{station_id}"
            recipients = station.get("recipients", global_recipients)

            try:
                fetched = fetch_station_status_playwright(page, url)
                status = fetched["status"]
                observed_text = fetched["observed_text"]
            except Exception as exc:
                status = "error"
                observed_text = str(exc)

            print(f"[CHECK] {station['name']} ({station_id}) => status={status}, text={observed_text}")

            # State transitions
            if status == "offline":
                entry["consecutive_offline"] = entry.get("consecutive_offline", 0) + 1
            elif status == "online":
                if entry.get("alert_sent"):
                    # Recovery: send recovery email and log
                    print(f"[RECOVERED] {station['name']} ({station_id}) back online at {now}")
                    send_email(
                        subject=f"[RECOVERED] {station['name']} ({station_id})",
                        body=(
                            f"Station {station['name']} ({station_id}) appears to be back online.\n"
                            f"URL: {url}\n"
                            f"Checked at: {now}\n"
                            f"Details: {observed_text or 'n/a'}\n"
                        ),
                        smtp_cfg=smtp_cfg,
                        recipients=recipients,
                    )
                entry["consecutive_offline"] = 0
                entry["alert_sent"] = False

            # Offline alert
            if status == "offline" and entry["consecutive_offline"] >= threshold and not entry.get("alert_sent"):
                print(
                    f"[OFFLINE] {station['name']} ({station_id}) appears offline "
                    f"(checks={entry['consecutive_offline']}) at {now}"
                )
                send_email(
                    subject=f"[OFFLINE] {station['name']} ({station_id})",
                    body=(
                        f"Station {station['name']} ({station_id}) appears OFFLINE on Weather Underground.\n"
                        f"URL: {url}\n"
                        f"Checked at: {now}\n"
                        f"Consecutive offline checks: {entry['consecutive_offline']}\n"
                        f"Details: {observed_text or 'n/a'}\n"
                    ),
                    smtp_cfg=smtp_cfg,
                    recipients=recipients,
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

            time.sleep(config.get("delay_seconds", 0.75))

        browser.close()

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