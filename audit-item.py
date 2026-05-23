#!/usr/bin/env python3

import sys
import os
import toml
import boto3
import subprocess
import argparse
import urllib.request
import bhl_aws_common
from pathlib import Path
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

def main():
    parser = argparse.ArgumentParser(
        description='Return a summary of AWS content for a BHL Item.'
    )
    parser.add_argument(
        'identifier',
        help='Archive.org identifier or BHL ItemID.'
    )
    parser.add_argument(
        '-c', '--csv',
        action='store_true',
        help='Output results in CSV format.'
    )
    args = parser.parse_args()

    if args.identifier is None: 
        print("No identifier found or provided.")
        sys.exit(64)

    result = bhl_aws_common.audit_item(args.identifier, config)

    if args.csv:
        print(f"identifier,tag,jp2_count,scandata_good,ocr_good,webp_good,scandata_images_good")
        print(f"{result['identifier']},{result['tag']},{result['jp2_count']},{result['scandata_good']},{result['ocr_good']},{result['webp_good']},{result['scandata_images_good']}")
    else :
        print(f"Summary:         {result['identifier']} ({result['tag']})")
        print(f"  JP2 Files:     {result['jp2_count']}")
        print(f"  IA Scandata:   {result['ia_pages_count']} Images")
        print(f"  AWS Scandata:  {result['aws_pages_count']} Images")
        print(f"  Scandata File: {result['scandata_good']}: Actual: {result['scandata_count']} Expected: 1")
        print(f"  WEBP Files:    {result['webp_good']}: Actual: {result['webp_count']} Expected: {result['expected_webp']}")
        print(f"  OCR Files:     {result['ocr_good']}: Actual: {result['ocr_count']} Expected: {result['expected_ocr']}")

if __name__ == "__main__":
    main()
