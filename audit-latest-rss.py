#!/usr/bin/env python3
"""
Update a BHL Item at AWS.

If uploading images, reads scandata.xml and jp2.zip from IA,
renames JP2 files sequentially when <addToAccessFormats> is true.
Then converts each JP2 to a variety of smaller sized WebP files.

If uploading scandata,
"""
import sys
import xml.etree.ElementTree as ET
import argparse
import toml
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

def main():
    url = 'http://www.biodiversitylibrary.org/RecentRss/500'
    temp_file = bhl_aws_common.download_url(url, config['general']['scratch_path'])
    if temp_file is not None:
        root = ET.parse(temp_file)
        pages = []

        namespace = bhl_aws_common.get_namespace(root.getroot())

        # Find all page elements
        for link in root.findall('.//{0}link'.format(namespace)):
            link = link.text.split('/')
            if len(link) > 4:
                result = bhl_aws_common.audit_item(link[4], config)
                if "virtual_item" in result['tag']:
                    result['tag'] = 'VIRTUAL_ITEM'
                    
                print(f"{result['identifier']},{result['tag']},{result['jp2_count']},{result['scandata_good']},{result['ocr_good']},{result['webp_good']}")


if __name__ == "__main__":
    main()

