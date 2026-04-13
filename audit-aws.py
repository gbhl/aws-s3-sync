#!/usr/bin/env python3

import sys
import os
import toml
import subprocess
import urllib.request
from pathlib import Path
from bhl_aws_common import download_url
from bhl_object import BHL_Object


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


    bhl_object = BHL_Object(config, Identifier=identifier)
    if bhl_object.object is None:
        print("Identifier is not in BHL. Stopping.")
        sys.exit(1)

    if bhl_object.type == 'virtual_item':
        print(f"{Identifier} is a virtual item. Stopping.")
    id_zfill = str(bhl_object.id).zfill(6)
    tag = f"{bhl_object.type}-{id_zfill}"

    jp2_count      = s3_count(f"s3://bhl-open-data/images/{identifier}/")
    scandata_count = s3_count(f"s3://bhl-open-data/scandata/{identifier}_scandata.xml")
    ocr_count      = s3_count(f"s3://bhl-open-data/ocr/{tag}/")
    webp_count     = s3_count(f"s3://bhl-open-data/web/{identifier}/")



    scandata_good = 'OK' if (scandata_count == 1) else 'Not OK'
    ocr_good = 'OK' if (jp2_count > 0 and (jp2_count + 1) == ocr_count) else 'Not OK'
    webp_good =  'OK' if (jp2_count > 0 and (jp2_count * 5) == webp_count) else 'Not OK'

    print(f"Item Summary:          {identifier} ({tag})")
    print(f"JP2 Files:             {jp2_count}")
    print(f"Scandata:  (1)         {scandata_count} ({scandata_good})")
    print(f"OCR Files  (JP2 + 1):  {ocr_count} ({ocr_good})")
    print(f"WEBP Files (JP2 * 5):  {webp_count} ({webp_good})")


if __name__ == "__main__":
    main()
