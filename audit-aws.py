#!/usr/bin/env python3

import sys
import os
import toml
import boto3
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

s3_session = boto3.Session('default')
s3_client = boto3.client('s3', aws_session_token=s3_session)


def count_s3_items(bucket, prefix, filter=None):
    """
    Count the number of items (objects) in a given AWS S3 path with
    an optional filter string.
    """
    paginator = s3_client.get_paginator('list_objects_v2')
    count = 0
    try:
        # Paginate through all objects matching the prefix
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            # Check if the page contains any objects
            if 'Contents' in page:
                for obj in page['Contents']:
                    # Apply filter if specified
                    if filter is None or filter in obj['Key']:
                        count += 1
        
        return count
    
    except s3_client.exceptions.NoSuchBucket:
        raise ValueError(f"Bucket '{bucket_name}' does not exist")
    except Exception as e:
        raise Exception(f"Error counting S3 items: {str(e)}")


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

    jp2_count      = count_s3_items("bhl-open-data", f"images/{identifier}/", ".jp2")
    scandata_count = count_s3_items("bhl-open-data", f"scandata/{identifier}_scandata.xml", ".xml")
    ocr_count      = count_s3_items("bhl-open-data", f"ocr/{tag}/", ".txt")
    webp_count     = count_s3_items("bhl-open-data", f"web/{identifier}/", ".webp")

    scandata_good = 'OK' if (scandata_count >= 1) else 'Not OK'
    ocr_good = 'OK' if (jp2_count > 0 and (jp2_count + 1) <= ocr_count) else 'Not OK'
    webp_good =  'OK' if (jp2_count > 0 and (jp2_count * 5) <= webp_count) else 'Not OK'
    expected_ocr = jp2_count + 1
    expected_webp = jp2_count * 5

    print(f"Summary:       {identifier} ({tag})")
    print(f"  JP2 Files:   {jp2_count}")
    print( "  ------------ Actual / Expected / (OK/Not-OK)")
    print(f"  Scandata:    {scandata_count}/1 ({scandata_good})")
    print(f"  OCR Files:   {ocr_count}/{expected_ocr} ({ocr_good})")
    print(f"  WEBP Files:  {webp_count}/{expected_webp} ({webp_good})")

if __name__ == "__main__":
    main()
