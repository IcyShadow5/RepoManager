"""Regenerate policy/patterns.json from the engine. Run after any regex edit."""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import attribution_policy as P  # noqa: E402

OUT = os.path.join(HERE, "patterns.json")


def main() -> int:
    data = {
        "policy_id": P.POLICY_ID,
        "policy_version": P.POLICY_VERSION,
        "flavour": "RE2",
        "operator": "regex",
        "negate": False,
        "description": (
            "GitHub ruleset commit_message_pattern. negate=false means the "
            "rule FAILS when the pattern matches, i.e. MUST NOT MATCH."
        ),
        "availability": (
            "commit_message_pattern is a GitHub Enterprise Cloud feature. On "
            "GitHub Free/Pro/Team the API rejects it with HTTP 422 "
            "\"Invalid rule 'commit_message_pattern'\", so this pattern is "
            "held ready for when the plan provides it."
        ),
        "commit_message_pattern": P.RE2_COMMIT_MESSAGE_PATTERN,
        "author_email_pattern": r"(?i)^.*@codebuff\.com$",
        "committer_email_pattern": r"(?i)^.*@codebuff\.com$",
        "author_name_pattern": r"(?i)(?:code[\s_-]?buff|free[\s_-]?buff)",
        "committer_name_pattern": r"(?i)(?:code[\s_-]?buff|free[\s_-]?buff)",
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
