#!/usr/bin/env python3
"""
Update a BHL Item at AWS.

If uploading images, reads scandata.xml and jp2.zip from IA,
renames JP2 files sequentially when <addToAccessFormats> is true.
Then converts each JP2 to a variety of smaller sized WebP files.

Optionally clean content at AWS before uploading, retains local files
that are created, and log output to the console as well as a log file.
"""
import sys
import os
import logging
import re
import pyvips
import boto3
import xml.etree.ElementTree as ET
import zipfile
import tarfile
import requests
import argparse
import shutil
import time
import json
import toml
import tempfile
import mimetypes
import gc
from PopLines import popHead
from pathlib import Path
from botocore.exceptions import NoCredentialsError
from random import randint
from wand.image import Image
from bhl_aws_common import download_url
from bhl_aws_common import parse_scandata
from bhl_aws_common import count_s3_items
from bhl_object import BHL_Object

# Read the config.toml file
config_file = Path('config.toml')
if not config_file.exists():
    print("config.toml not found.")
    sys.exit(1)

with open('config.toml', 'r') as f:
    config = toml.load(f)

# AWS Credentials come from the current user's ~/.aws/credentials file
# --------------
s3_session = boto3.Session('default')
s3_client = boto3.client('s3', aws_session_token=s3_session)

# Set up Logging
# --------------
tmp = Path(config['logging']['path'])
tmp.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=f"{tmp}/{config['logging']['filename']}",
    format="%(asctime)s: %(module)s (%(levelname)s): %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("update-aws-item")
logging.getLogger('pyvips').setLevel(logging.CRITICAL)
logging.getLogger('requests').setLevel(logging.CRITICAL)
logging.getLogger('botocore').setLevel(logging.CRITICAL)
logging.getLogger('boto3').setLevel(logging.CRITICAL)
logging.getLogger('s3transfer').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

# Reduce memory footprint - we don't need a lot of caching
# --------------
pyvips.cache_set_max(0)

# We need a place to save things while we work
# -------------- 
bhl_object = None

def get_identifier_by_index(idx):
    idx = int(idx)
    input_data = open(config['general']['id_list'], 'r')

    num = 1
    for row in input_data:
        if num == idx:
            identifier = row.rstrip('\n').strip()
            input_data.close()
            if identifier == "":
                return None
            return identifier
        num += 1
    input_data.close()
    return None

def get_cache_path(identifier, type):
    """
    This is used to normalize the paths for where we keep copies of data.
    Only the "metadata" item is meant to be preserved indefinitely.
    """
    pth = None
    # Contains content from https://archive.org/metadata/IDENTIFIER
    if type == 'metadata':
        pth = Path(config['general']['cache_path']) / 'json'

    # Contains content from https://archive.org/IDENTIFIER/IDENTIFIER_scandata.xml
    # or a renamed scandata.zip/scandata.xml.
    if type == 'scandata':
        pth = Path(config['general']['cache_path']) / 'xml'

    # Guaranteed to contain JP2 files, but not in scandata order.
    if type == 'images':
        pth = Path(config['general']['cache_path']) / 'jp2'

    # Contains item-000000 or part-000000 folders with OCR. Also may contain
    # the combined OCR as an item-000000.txt file (with no Sequence or PageIDs)
    if type == 'ocr':
        pth = Path(config['general']['cache_path']) / 'ocr'

    if pth is None:
        return None

    pth.mkdir(parents=True, exist_ok=True)    
    return pth

def create_jpg_files(identifier, input_dir, jpeg_output_dir):
    """
    Process all JP2 images in the directory input_dir saing to output_dir
    """
    # TODO Find the memory leak when creating WEBP files
    input_dir = Path(input_dir)

    # Make sure this exists, it should already exist
    if not input_dir.exists():
        logger.error(f"Directory '{input_dir}' does not exist")
        return

    # Find all JP2 files
    jp2_files = list(input_dir.glob('*.jp2')) + list(input_dir.glob('*.JP2'))
    jp2_files.sort()

    if not jp2_files:
        logger.error(f"No JP2 files found in '{input_dir}'")
        sys.exit(1)

    # convert JP2 to full-size WEBP
    for j in jp2_files:

        jp2_path = Path(j)
        jp2_base = jp2_path.stem

        input_file = input_dir / f"{jp2_base}.jp2"
        jpg_output_file = jpeg_output_dir / f"{jp2_base}.jpg"

        # Save full size webp
        img = pyvips.Image.new_from_file(input_file, access='sequential')
        if not jpg_output_file.exists():
            img.write_to_file(jpg_output_file, Q=config['general']['jpeg_quality'])

    return(str(jpeg_output_dir))

def sync_dir_to_aws_s3(source_path, pattern, bucket, prefix):
    s3_client = boto3.client('s3')
    upload_files = list(source_path.glob(pattern))
    upload_files.sort()

    for file in upload_files:
        fsplit = os.path.split(file)
        filename = fsplit[1]
        s3_object_name = f"{prefix}/{filename}"

        try:
            logger.debug(f"Syncing to S3: {file} --> s3://{bucket}/{s3_object_name}")
            m_type = mimetypes.guess_type(file)
            response = s3_client.upload_file(file, bucket, s3_object_name, {"ContentType": m_type[0], "StorageClass": "INTELLIGENT_TIERING"})
        except NoCredentialsError:
            logger.error('Credentials not available')
        except Exception as e:
            logger.error(e)

def get_aws_jp2_files(identifier, jp2_dir, bucket):
    s3 = boto3.resource('s3')

    bucket = s3.Bucket(bucket)

    key = f"images/{identifier}/"
    objs = list(bucket.objects.filter(Prefix=key))

    for obj in objs:
        filename = jp2_dir / os.path.split(obj.key)[1]
        if not filename.exists():
            bucket.download_file(obj.key, f"{filename}")
        

def update_item(Identifier=None, StdOut=False, Verbose=False):
    # -------------------
    # Update the logger to write to logs/IDENTIFIER.log
    # -------------------
    global bhl_object
    global logger

    # remove all old handlers
    logging.getLogger().removeHandler(logging.getLogger().handlers[0])

    # Send all the logging to a new file
    formatter = logging.Formatter('%(asctime)s: %(name)s: (%(levelname)s) %(message)s')
    fileh = logging.FileHandler("{0}/{1}.log".format(Path(config['logging']['path']), Identifier), 'a')
    fileh.setFormatter(formatter)
    logger.addHandler(fileh)

    if StdOut:
        # Also send to stdout if directed to
        fileout = logging.StreamHandler(sys.stdout)
        fileout.setFormatter(formatter)
        logger.addHandler(fileout)

    if Verbose:
        # also send more noise if directed to
        logger.setLevel(logging.DEBUG)

    # ---------------
    # Let's goooo!
    # ---------------
    try:
        logger.info('Download JP2')
        jp2_dir = Path(config['general']['scratch_path']) / Identifier / "jp2"
        jp2_dir.mkdir(parents=True, exist_ok=True)
        get_aws_jp2_files(Identifier, jp2_dir, 'bhl-open-data')

        logger.info('Convert to JPG')
        jpg_dir = Path(config['general']['scratch_path']) / Identifier / "jpg"
        jpg_dir.mkdir(parents=True, exist_ok=True)
        create_jpg_files(Identifier, jp2_dir, jpg_dir)

        logger.info('Upload to AWS')
        sync_dir_to_aws_s3(jpg_dir, '*.jpg', 'bhl-open-data', f"jpg/{Identifier}")

        logger.info('Cleanup')
        shutil.rmtree(Path('tmp') / Identifier)

    except Exception as e:
        logger.error(e)

def main():
    global bhl_object
    # Parse the command line
    # ----------------------
    parser = argparse.ArgumentParser(
        description='Update a BHL item at AWS. Optionally only update parts of the item.'
    )
    parser.add_argument(
        '--identifier',
        default=None,
        required=False,
        help='Archive.org identifier for the item.'
    )
    parser.add_argument(
        '-d', '--stdout',
        action='store_true',
        help='Output to STDOUT as well as the log file'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Output more info. (logging=DEBUG)'
    )
    args = parser.parse_args()

    # Make sure this exists
    tmp = Path(config['general']['scratch_path'])
    tmp.mkdir(parents=True, exist_ok=True)

    # If we got an identifier from the command line, use that.
    Identifier = None
    Identifier = args.identifier

    if Identifier is None:
        print("No identifier found or provided.")
        sys.exit(2)

    update_item(
        Identifier = Identifier,
        StdOut = args.stdout,
        Verbose = args.verbose
    )

if __name__ == "__main__":
    main()
