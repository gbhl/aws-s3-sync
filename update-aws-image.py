#!/usr/bin/env python3
"""
Update a BHL Item at AWS.

If uploading images, reads scandata.xml and jp2.zip from IA,
renames JP2 files sequentially when <addToAccessFormats> is true.
Then converts each JP2 to a variety of smaller sized WebP files.

If uploading scandata,
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
from PopLines import popHead
from pathlib import Path
from botocore.exceptions import NoCredentialsError
from random import randint
from wand.image import Image
from bhl_aws_common import download_url
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
logger = logging.getLogger("update-aws-image")
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

def create_webp_file(filename, output_dir):
    """
    Process all JP2 images in the directory input_dir saing to output_dir
    """
    webp_files = []

    jp2_path = Path(filename)
    print(f"jp2_path = {jp2_path}")
    jp2_base = jp2_path.stem
    print(f"jp2_base = {jp2_base}")
    sys.exit()

    input_file = input_dir / f"{jp2_base}.jp2"
    output_file = output_dir / f"{jp2_base}_full.webp"

    logger.debug(f"WebP Source: {jp2_base}.jp2")

    # Save full size webp
    try:
        img = pyvips.Image.new_from_file(input_file, access='sequential')
        img_w = img.width
        img_h = img.height

        # webp images are limited to 16383 x 16383 but this will cause memory problems
        # Scale the image down per the config setting
        # Note: This will still use up to 4 GB of resident RAM. 
        img_max_pixels = int(config['general']['max_image_dimension'])
        if img_w > img_max_pixels or img_h > img_max_pixels:
            logger.warning(f"Image {input_file} is too large ({img_w}x{img_h}). Resizing to {img_max_pixels} px.")
            img = img.resize(img_max_pixels / max(img_w, img_h))
            # get the sizes again since we resized the original file
            img_w = img.width
            img_h = img.height

        logger.debug(f"Image {input_file} size: {img_w} x {img_h}")
        if not output_file.exists():
            img.write_to_file(output_file, Q=config['general']['webp_quality'])
            webp_files.append(output_file)

        # Since we use 'input_file' below for the thumbnails, point us to
        # the webp file we just created
        input_file = output_file


    except Exception as e:
        try:
            logger.error(f"VIPS error for {jp2_base}.jp2: {e}")
            logger.error('Falling back to ImageMagic/Wand')
            # Something went wrong, fall back to ImagageMagick Wand module
            img = Image(filename=input_file)
            img_w, img_h = img.size

            if img_w > img_max_pixels:
                logger.warning(f"Image too large ({img_w}x{img_h}) Resizing to {img_max_pixels} px.")
                img = img.resize(img_max_pixels, int(img_h * (img_max_pixels / img_w)))
                img_w, img_h = img.size
            elif img_h > img_max_pixels:
                logger.warning(f"Image too large ({img_w}x{img_h}) Resizing to {img_max_pixels} px.")
                img = img.resize(int(img_w * (img_max_pixels / img_h)), img_max_pixels)
                img_w, img_h = img.size

            img.compression_quality = config['general']['webp_quality']
            img_webp = img.convert('webp')
            if not output_file.exists():
                img_webp.save(filename=output_file)
                webp_files.append(output_file)

            # Since we use 'input_file' below for the thumbnails, point us to
            # the webp file we just created
            input_file = output_file
        except Exception as e:
            logger.error(f"File {output_file} could not be saved. Continuing.")

    # resize to webp
    for size_name in config['webp_sizes']:
        target_width = config['webp_sizes'][size_name]
        # calculate the resize factors using the current width and the desired width
        # We don't upscale. So ensure the factor isn't < 1
        factor = min(target_width / img_w, 1)
        th_h = int(factor * img_h)
        th_w = int(factor * img_w)
        logger.debug(f"WebP Source: {jp2_base}.jp2 -> {size_name} ({th_w}x{th_h}, {factor})")

        thumb_file = output_dir / f"{jp2_base}_{size_name}.webp"

        try:
            if not thumb_file.exists():
                # Image.thumbnail scales to a square. Use max() to handle landscape images.
                thumb = pyvips.Image.thumbnail(input_file, max(th_w, th_h))

                thumb.write_to_file(thumb_file)
            
            if os.path.getsize(thumb_file) == 0:
                logger.warning(f"Thumbnail {input_file} was empty.")
                # Image.thumbnail scales to a square. Use max() to handle landscape images.
                thumb = pyvips.Image.thumbnail(input_file, max(th_w, th_h))
                thumb.write_to_file(thumb_file)
        except Exception as e:
            try: 
                # Something went wrong, fall back to ImagageMagick Wand module
                logger.error(f"VIPS error for thumbnal {jp2_base}.jp2: {e}")
                logger.error('Falling back to ImageMagic/Wand')
                img = Image(filename=input_file)
                img = img.resize(th_w, th_h)
                img.compression_quality = config['general']['webp_quality']
                img_webp = img.convert('webp')
                img_webp.save(filename=thumb_file)
            except Exception as e:
                logger.error(f"File {thumb_file} could not be saved. Continuing.")    

    return(webp_files)

def sync_file_to_aws_s3(source_file, bucket, prefix):
    s3_client = boto3.client('s3')
    s3_object_name = prefix + '/' + os.path.basename(source_file)

    try:
        logger.debug(f"Syncing to S3: {source_file} --> s3://{bucket}/{s3_object_name}")
        m_type = mimetypes.guess_type(source_file)
        response = s3_client.upload_file(source_file, bucket, s3_object_name, {"ContentType": m_type[0], "StorageClass": "INTELLIGENT_TIERING"})
    except NoCredentialsError:
        logger.error('Credentials not available')
    except Exception as e:
        logger.error(e)
        
def get_aws_file(bucket, prefix, file, dest_file):
    s3_client = boto3.client('s3')
    s3_object_name = prefix + '/' + file
    try:
        logger.debug(f"Downloading from S3: s3://{bucket}/{s3_object_name}")
        with open(dest_file, 'wb') as fh_dest:
            response = s3.download_fileobj(bucket, s3_object_name, fh_dest)

    except NoCredentialsError:
        logger.error('Credentials not available')
    except Exception as e:
        logger.error(e)

def update_image(FileName=None, StdOut=False, Verbose=False, DryRun=False):

    # Update Logging
    if StdOut:
        # Also send to stdout if directed to
        fileout = logging.StreamHandler(sys.stdout)
        fileout.setFormatter(formatter)
        logger.addHandler(fileout)

    if Verbose:
        # also send more noise if directed to
        logger.setLevel(logging.DEBUG)


    tmp = Path(config['general']['scratch_path'])
    Identifier = FileName.split('_')[0]
    dest = tmp / FileName
    # Get the image from AWS
    get_aws_file('bhl-open-data', f"images/{Identifier}", FileName, dest)
    sys.exit()

    # Convert to WebP
    webp_files = create_webp_file(dest, tmp)
    sys.exit()

    # Upload WebP to AWS
    for f in webp_files:
        sync_file_to_aws_s3(f, 'bhl-open-data', f"web/{Identifier}")

    sys.exit()

    # Cleanup
    os.remove(dest)
    for f in webp_files:
        os.remove(f)


def main():
    global bhl_object
    # Parse the command line
    # ----------------------
    parser = argparse.ArgumentParser(
        description='Updates WebP derivatives for one image at AWS.'
    )
    parser.add_argument(
        '--file',
        default=None,
        required=False,
        help='JP2 Filename for one image. Example: IDENTIFIER_0001.jp2'
    )
    parser.add_argument(
        '--stdout',
        action='store_true',
        help='Output to STDOUT as well as the log file'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Output more info. (logging=DEBUG)'
    )
    parser.add_argument(
        '--dryrun',
        action='store_true',
        help='Do everything except upload to AWS'
    )
    args = parser.parse_args()

    # Make sure this exists
    tmp = Path(config['general']['scratch_path'])
    tmp.mkdir(parents=True, exist_ok=True)

    Identifier = None
    # If we got an identifier from the command line, use that.
    if args.file:
        FileName = args.file
        logging.info(f"Processing {FileName}")

    if FileName is None: 
        print("No filename provided.")
        sys.exit(64)

    update_image(
        FileName = FileName,
        StdOut = args.stdout,
        Verbose = args.verbose,
        DryRun = args.dryrun,
    )

if __name__ == "__main__":
    main()
