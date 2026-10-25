"""Scan tracked files; print locations only, never candidate secret values."""

import subprocess

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings


def main():
    paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    collection = SecretsCollection()
    with default_settings() as settings:
        # Exact translated UI labels, not credential values. JSON cannot use comments.
        settings.filters["detect_secrets.filters.regex.should_exclude_line"] = {
            "pattern": [
                r'^\s*"password": "(?:Card password|Dedicated broker password \(up to 32 ASCII bytes\))"[,]?\s*$'
            ]
        }
        for path in filter(None, paths):
            collection.scan_file(path)
    findings = list(collection)
    for filename, secret in findings:
        print(f"{filename}:{secret.line_number}: review potential {secret.type}")
    print(f"Scanned {len(paths) - 1} tracked files; {len(findings)} findings.")
    raise SystemExit(bool(findings))


if __name__ == "__main__":
    main()
