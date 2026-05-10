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
        print(f"{result['identifier']},{result['tag']},{result['jp2_count']},{result['scandata_good']},{result['ocr_good']},{result['webp_good']}")
    else :
        print(f"Summary:       {result['identifier']} ({result['tag']})")
        print(f"  JP2 Files:   {result['jp2_count']}")
        print( "  ------------ Actual / Expected")
        print(f"  Scandata:    {result['scandata_count']}/1 ({result['scandata_good']})")
        print(f"  OCR Files:   {result['ocr_count']}/{result['expected_ocr']} ({result['ocr_good']})")
        print(f"  WEBP Files:  {result['webp_count']}/{result['expected_webp']} ({result['webp_good']})")

if __name__ == "__main__":
    main()
