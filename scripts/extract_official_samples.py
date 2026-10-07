"""Extract full payload examples from a saved GitLab documentation page."""

import argparse
import json
from pathlib import Path

from bs4 import BeautifulSoup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--system", action="store_true", help="Extract System Hook examples")
    args = parser.parse_args()
    soup = BeautifulSoup(args.html.read_text(encoding="utf-8"), "html.parser")
    section = ""
    header = ""
    samples = []
    for tag in soup.find_all(["h2", "h3", "pre"]):
        if tag.name != "pre":
            section = tag.get("id", "")
            continue
        raw = tag.get_text().strip()
        if raw.startswith("X-Gitlab-Event:"):
            header = raw.partition(":")[2].strip()
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        # Partial MR snippets are validated separately against the complete example.
        if not isinstance(payload, dict) or not ("project" in payload or "group" in payload or "event_name" in payload or payload.get("object_kind") == "duo_workflow" or (args.system and payload.get("object_kind", "").startswith("gitlab_subscription_member_approval"))):
            continue
        samples.append({"section": section, "header": header, "payload": payload})
    source = "https://docs.gitlab.com/administration/system_hooks/" if args.system else "https://docs.gitlab.com/user/project/integrations/webhook_events/"
    output = {"source": source, "samples": samples}
    if args.system:
        heading = soup.find(id="triggered-events")
        table = heading.find_next("table")
        output["documented_events"] = [row.find("td").get_text(strip=True) for row in table.find_all("tr") if row.find("td")]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(samples)} full official examples")


if __name__ == "__main__":
    main()
