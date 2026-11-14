"""Command‑line interface for the attack_surface package.

Usage:
    python -m attack_surface.cli <path-to-file>

The CLI prints the metadata dictionary as JSON to stdout.
"""

import argparse
import json
import sys

from .metadata import extract_metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract metadata from a media file")
    parser.add_argument(
        "file_path",
        type=str,
        help="Path to the file to analyze",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty‑print the JSON output",
    )
    args = parser.parse_args(argv)

    try:
        metadata = extract_metadata(args.file_path)
    except FileNotFoundError as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"Unexpected error: {exc}\n")
        sys.exit(1)

    json_output = json.dumps(metadata, indent=2) if args.pretty else json.dumps(metadata)
    print(json_output)


if __name__ == "__main__":
    main()
