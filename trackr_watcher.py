"""Trackr Spring-Week-Watcher.

Fragt die öffentliche Trackr-API ab und schickt für jedes Spring-Week-Programm,
dessen Bewerbung neu geöffnet hat, eine Mail mit Betreff "Spring week <Firma>".

Konfiguration über Umgebungsvariablen:
  GMAIL_ADDRESS       Gmail-Adresse, von der gesendet wird
  GMAIL_APP_PASSWORD  App-Passwort dieses Gmail-Kontos
  MAIL_TO             Empfänger, mehrere mit Komma getrennt (optional, Standard: GMAIL_ADDRESS)
  SEASON              Saison, z. B. 2027 (optional, Standard: unten)
  DRY_RUN=1           nichts senden, nur ausgeben
"""

import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

DEFAULT_SEASON = "2027"
API_URL = (
    "https://api.the-trackr.com/programmes"
    "?region=UK&industry=Finance&season={season}&type=spring-weeks"
)
STATE_FILE = Path(__file__).with_name("state.json")
# Öffentliche GitHub-Repos deaktivieren Zeitpläne nach 60 Tagen ohne Aktivität.
# Deshalb wird state.json spätestens nach so vielen Tagen neu geschrieben (= Commit).
HEARTBEAT_DAYS = 30


def fetch_programmes(season):
    req = urllib.request.Request(
        API_URL.format(season=season),
        headers={"User-Agent": "spring-week-watcher/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["programmes"]


def parse_date(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_open(programme, now):
    opening = parse_date(programme.get("openingDate"))
    closing = parse_date(programme.get("closingDate"))
    if opening is None or opening > now:
        return False
    # Schlusstag zählt noch als offen
    return closing is None or closing + timedelta(days=1) > now


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return None


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def fmt_date(value):
    d = parse_date(value)
    return d.strftime("%d.%m.%Y") if d else "–"


def build_mail(programme, sender, recipients):
    company = programme.get("company", {}).get("name") or programme.get("companyId", "?")
    msg = EmailMessage()
    msg["Subject"] = f"Spring week {company}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    lines = [
        f"Die Bewerbung für eine Spring Week bei {company} ist jetzt offen!",
        "",
        f"Programm:      {programme.get('name')}",
        f"Geöffnet am:   {fmt_date(programme.get('openingDate'))}",
        f"Deadline:      {fmt_date(programme.get('closingDate'))}",
        f"Rolling:       {'Ja' if programme.get('rolling') else 'Nein'}",
    ]
    if programme.get("eligibility"):
        lines.append(f"Zielgruppe:    {programme['eligibility']}")
    if programme.get("process"):
        lines.append(f"Prozess:       {' › '.join(programme['process'])}")
    if programme.get("coverLetter") and programme["coverLetter"] != "No":
        lines.append(f"Cover Letter:  {programme['coverLetter']}")
    if programme.get("notes"):
        lines.append(f"Notizen:       {programme['notes']}")
    lines += [
        "",
        f"Bewerben: {programme.get('url') or '–'}",
        "Trackr:   https://app.the-trackr.com/uk-finance/spring-weeks",
    ]
    msg.set_content("\n".join(lines))
    return msg


def send_mails(messages, sender, password):
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, password)
        for msg in messages:
            smtp.send_message(msg)


def main():
    dry_run = os.environ.get("DRY_RUN") == "1"
    season = os.environ.get("SEASON") or DEFAULT_SEASON
    sender = os.environ.get("GMAIL_ADDRESS", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    recipients = [a.strip() for a in (os.environ.get("MAIL_TO") or sender).split(",") if a.strip()]
    if not dry_run and not (sender and password):
        sys.exit("GMAIL_ADDRESS und GMAIL_APP_PASSWORD müssen gesetzt sein.")

    now = datetime.now(timezone.utc)
    programmes = fetch_programmes(season)
    open_now = {p["id"]: p for p in programmes if is_open(p, now)}
    print(f"{len(programmes)} Programme, davon {len(open_now)} offen.")

    state = load_state()
    if state is None or state.get("season") != season:
        # Erster Lauf (oder neue Saison): bereits offene merken, keine Mail-Flut.
        save_state({"season": season, "notified": sorted(open_now), "heartbeat": now.date().isoformat()})
        print("Startzustand gespeichert, keine Mails gesendet.")
        return

    notified = set(state["notified"])
    new = [p for pid, p in open_now.items() if pid not in notified]
    for p in new:
        print(f"NEU OFFEN: {p.get('company', {}).get('name')} – {p.get('name')}")

    if new:
        messages = [build_mail(p, sender, recipients) for p in new]
        if dry_run:
            for m in messages:
                print("----", m["Subject"], "----\n" + m.get_content())
        else:
            send_mails(messages, sender, password)
            print(f"{len(messages)} Mail(s) gesendet.")
        notified.update(p["id"] for p in new)

    heartbeat_due = (now.date() - datetime.fromisoformat(state["heartbeat"]).date()).days >= HEARTBEAT_DAYS
    if new or heartbeat_due:
        state["notified"] = sorted(notified)
        state["heartbeat"] = now.date().isoformat()
        if not dry_run:
            save_state(state)


if __name__ == "__main__":
    main()
