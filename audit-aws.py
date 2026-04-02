#!/usr/bin/env python3

import sys
import os
import toml
import subprocess
import urllib.request
from pathlib import Path
import bhl_aws_common

# Read the config.toml file
config_file = Path('config.toml')
if not config_file.exists():
    print("config.toml not found.")
    sys.exit(1)

with open('config.toml', 'r') as f:
    config = toml.load(f)

def s3_count(path):
    """Return the number of lines returned by `aws s3 ls <path>`."""
    result = subprocess.run(
        ["aws", "s3", "ls", path],
        capture_output=True,
        text=True,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    return len(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python bhl_summary.py IDENTIFIER")
        sys.exit(1)

    identifier = sys.argv[1]

    os.makedirs("cache/data", exist_ok=True)


    (bhl_type, bhl_id, bhl_item) = bhl_aws_common.get_bhl_item(config, Identifier=identifier, OCR=True)
    if bhl_item is None:
        (bhl_type, bhl_id, bhl_item) = bhl_aws_common.get_bhl_part(config, Identifier=identifier, OCR=True)
        if bhl_item is None:
            print("Identifier is not in BHL. Stopping.")
            sys.exit(1)
        tag = f"part-{bhl_id}"
    else:
        tag = f"item-{bhl_id}"


    jp2_count      = s3_count(f"s3://bhl-open-data/images/{identifier}/")
    scandata_count = s3_count(f"s3://bhl-open-data/scandata/{identifier}_scandata.xml")
    ocr_count      = s3_count(f"s3://bhl-open-data/ocr/{tag}/")
    webp_count     = s3_count(f"s3://bhl-open-data/web/{identifier}/")

    print(f"Item Summary:          {identifier} ({tag})")
    print(f"JP2 Files:             {jp2_count}")
    print(f"Scandata:  (1)         {scandata_count}")
    print(f"OCR Files  (JP2 + 1):  {ocr_count}")
    print(f"WEBP Files (JP2 * 5):  {webp_count}")


if __name__ == "__main__":
    main()
