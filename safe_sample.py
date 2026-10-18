"""Read-only ConnectRobo diagnostic; no login, click, send, or network code.

Original project: dannyb66/connectrobo; Pavlo Bondarenko (MIT).
"""
import argparse
import json
from html.parser import HTMLParser
from pathlib import Path


class Buttons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.active = None

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.active = {"text": "", "label": dict(attrs).get("aria-label", "")}

    def handle_data(self, data):
        if self.active is not None:
            self.active["text"] += data

    def handle_endtag(self, tag):
        if tag == "button" and self.active is not None:
            self.items.append(self.active)
            self.active = None


def validate_config(config):
    expected = {"mode": "offline_no_send", "max_pages": 1, "credentials": None,
                "external_navigation": False, "allowed_mutations": []}
    if config != expected:
        raise ValueError("Only the exact offline no-send configuration is supported")


def inspect_html(html):
    parser = Buttons()
    parser.feed(html)
    counts = {"connect": 0, "follow": 0, "send": 0, "next": 0, "other": 0}
    legacy_matches = {"connect": 0, "follow": 0}
    for item in parser.items:
        text = " ".join(item["text"].split()).casefold()
        label = " ".join(item["label"].split()).casefold()
        if text in legacy_matches:
            legacy_matches[text] += 1
        name = label or text
        kind = next((k for k in ("connect", "follow", "send", "next")
                     if name == k or name.startswith(k + " ")), "other")
        counts[kind] += 1
    # Never return names, profile links, messages, credentials, or raw HTML.
    return {"mode": "offline_no_send", "source": "synthetic_fixture",
            "buttons": counts, "legacy_exact_text_matches": legacy_matches,
            "actions_executed": 0, "external_requests": 0,
            "result": "PASS: observation only; all mutations omitted"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=Path(__file__).with_name("safe_config.json"))
    args = p.parse_args()
    validate_config(json.loads(args.config.read_text()))
    # Intentionally fixed local fixture: no arbitrary URL, browser or login option.
    fixture = Path(__file__).with_name("fixture.html")
    print(json.dumps(inspect_html(fixture.read_text()), indent=2))


if __name__ == "__main__":
    main()
