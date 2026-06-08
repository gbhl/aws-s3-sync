#!/usr/bin/env python3

import sys
import os
import toml
import boto3
import subprocess
import argparse
import urllib.request
import bhl_aws_common
import xml.etree.ElementTree as ET
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

def print_header():
    print(f"identifier,tag,jp2_count,scandata_good,ocr_good,webp_good,scandata_images_good")

def print_results(data, as_csv=False, send_header=False):
    if as_csv:
        if send_header:
            print_header()

        print(f"{data['identifier']},{data['tag']},{data['jp2_count']},{data['scandata_good']},{data['ocr_good']},{data['webp_good']},{data['scandata_images_good']}")
    else :
        print(f"Summary:         {data['identifier']} ({data['tag']})")
        print(f"  JP2 Files:     {data['jp2_count']}")
        print(f"  IA Scandata:   {data['ia_pages_count']} Images")
        print(f"  AWS Scandata:  {data['aws_pages_count']} Images")
        print(f"  Scandata File: {data['scandata_good']}: Actual: {data['scandata_count']} Expected: 1")
        print(f"  WEBP Files:    {data['webp_good']}: Actual: {data['webp_count']} Expected: {data['expected_webp']}")
        print(f"  OCR Files:     {data['ocr_good']}: Actual: {data['ocr_count']} Expected: {data['expected_ocr']}")

def audit_one(identifier, id=None, as_csv=False, send_header=False):
    result = bhl_aws_common.audit_item(identifier, id, config)
    print_results(result, as_csv, send_header)

    if result['tag'] == 'VIRTUAL ITEM':
        bhl_object = BHL_Object(config, Identifier=identifier, ID=id)
        res = bhl_object.get_bhl_virtual_item()
        if res:
            for part in bhl_object.parts:
                result = bhl_aws_common.audit_item(part['SourceIdentifier'], None, config)
                print_results(result, as_csv, False)
        else:
            print(f"Could not get parts for virtual item {bhl_object.identifier} / {bhl_object.id}")

def audit_rss(as_csv=False, send_header=False):
    url = 'http://www.biodiversitylibrary.org/RecentRss/500'
    temp_file = bhl_aws_common.download_url(url, config['general']['scratch_path'])
    if temp_file is not None:
        root = ET.parse(temp_file)
        pages = []

        namespace = bhl_aws_common.get_namespace(root.getroot())

        if send_header:
            print_header()

        # Find all page elements
        for link in root.findall('.//{0}link'.format(namespace)):
            link = link.text.split('/')
            if len(link) > 4:
                audit_one(None, link[4], as_csv=as_csv, send_header=False)

def main():
    parser = argparse.ArgumentParser(
        description='Return a summary of AWS content for a BHL Item.'
    )
    parser.add_argument(
        '--identifier',
        default=None,
        required=False,
        help='Archive.org identifier for the item.'
    )
    parser.add_argument(
        '--id',
        default=None,
        required=False,
        help='BHL ItemID or PartID.'
    )
    parser.add_argument(
        '-c', '--csv',
        action='store_true',
        help='Output results in CSV format.'
    )
    parser.add_argument(
        '-d', '--header',
        action='store_true',
        help='Include CSV header row.'
    )
    parser.add_argument(
        '-r', '--rss',
        action='store_true',
        help='Use last 500 RSS Feed.'
    )
    args = parser.parse_args()

    if args.identifier is None and args.id is None and not args.rss: 
        print("No identifier found or provided.")
        sys.exit(64)

    if args.identifier is not None or args.id is not None:
        audit_one(args.identifier, args.id, as_csv=args.csv, send_header=args.header)
    elif args.rss: 
        audit_rss(args.csv, args.header)


if __name__ == "__main__":
    main()
