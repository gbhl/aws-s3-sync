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

def get_scandata(identifier):
    scandata_file = get_cache_path(identifier, 'scandata') / f"{identifier}_scandata.xml"
    download = False

    # If we don't have a scandata, download it.
    if not scandata_file.exists():
        download = True

    # Use the JSON metadata file to identify what to download
    json_file = download_file(identifier, "metadata")
    if json_file is None:
        logger.error('Could not get JSON')
        return None

    with open(json_file, 'r') as file:
        metadata = json.load(file)

    ia_scandata_file = None
    ia_scandata_mtime = None
    for file in metadata['files']:
        if file['format'] == 'Scandata' or file['format'] == 'Scribe Scandata ZIP':
            ia_scandata_file = file['name']
            # ia_scandata_mtime = file['mtime']

    if ia_scandata_file is None:
        # If there's no scandata, we have a problem
        logger.error('No scandata found at IA')
        return None

    if download:
        # TODO: how do we remember that we download from AWS? We don't need to re-upload later.        
        # Download it from AWS.
        url = f"https://bhl-open-data.s3.us-east-2.amazonaws.com/scandata/{identifier}_scandata.xml"
        
        temp_file = download_url(url, config['general']['scratch_path'], logger, config)
        if temp_file is not None:
            os.rename(temp_file, scandata_file)
            logger.debug('Downloaded scandata from AWS')
        else:
            url = f"https://archive.org/download/{identifier}/{ia_scandata_file}"
            if ia_scandata_file.endswith('.zip'):
                url = f"https://archive.org/download/{identifier}/{ia_scandata_file}/scandata.xml"
            temp_file = download_url(url, config['general']['scratch_path'], logger, config)
            if temp_file is not None:
                os.rename(temp_file, scandata_file)
                logger.debug('Downloaded scandata from IA')

    return scandata_file

def get_images(identifier):
    # TODO Modify this to pay attention to mtime and download from IA if what they have is newer

    images_file = get_cache_path(identifier, 'images') / f"{identifier}_jp2.zip"
    download = False
    download_reason = ''
    ret = None

    # If we don't have a images_file, download it.
    if not images_file.exists() or os.path.getsize(images_file) == 0:
        download = True
        download_reason = "File did not exist or was empty."

    # Use the JSON metadata file to identify what to download
    json_file = download_file(identifier, "metadata")
    if json_file is None:
        logger.error('Could not get JSON')
        return None

    # Figure out what file we want
    with open(json_file, 'r') as file:
        metadata = json.load(file)

    images_file = None
    images_filename = None
    images_mtime = None
    for file in metadata['files']:
        if (file['format'] == 'Single Page Processed JP2 Tar' or
            file['format'] == 'Single Page Processed JP2 ZIP'):
            images_filename = file['name']
            images_mtime = file['mtime']

    if images_filename is None:
        for file in metadata['files']:
            if (file['format'] == 'Single Page Processed TIFF ZIP'):
                images_filename = file['name']
                images_mtime = file['mtime']

    if images_filename is None:
        for file in metadata['files']:
            if (file['format'] == 'Single Page Processed TIFF TAR'):
                images_filename = file['name']
                images_mtime = file['mtime']

    if images_filename is None:
        for file in metadata['files']:
            if (file['format'] == 'Single Page Original TIFF ZIP'):
                images_filename = file['name']
                images_mtime = file['mtime']

    if download:
        # Download the images file
        if images_filename is None:
            logger.error('No images file found in scandata')
            return None
        
        images_file = get_cache_path(identifier, 'images') / images_filename
        # Check again if we really need to download
        if not images_file.exists() or os.path.getsize(images_file) == 0:
            url = f"https://archive.org/download/{identifier}/{images_filename}"
            temp_file = download_url(url, config['general']['scratch_path'], logger, config)
            if temp_file is None:
                logger.error('No image file found at IA')
                return None
                
            os.rename(temp_file, images_file)
            logger.debug(f"Downloaded images file: {download_reason}")

    else:
        logger.debug('Not downloading, JP2 file exists')

    if images_file is None:
        logger.error("Couldn't find images")
        return None

    return images_file

def download_file(identifier, type, use_cache=True):
    # TODO This function needs to be renamed. There's no downloading happening!
    if type == 'metadata':
        metadata_path = get_cache_path(identifier, type) / identifier[0:1]
        metadata_path.mkdir(parents=True, exist_ok=True)
        metadata_file = metadata_path / f"{identifier}.json"
        if not metadata_file.exists():
            temp_file = download_url(f"https://archive.org/metadata/{identifier}", config['general']['scratch_path'], logger, config)
            if temp_file is not None:
                os.rename(temp_file, metadata_file)
            else:
                return None
        return metadata_file

    if type == 'images':
        images_file = get_cache_path(identifier, type) / f"{identifier}_jp2.zip"
        if not images_file.exists():
            logger.debug('Downloading images')
            images_file = get_images(identifier)
        return images_file

    if type == 'scandata':
        scandata_file = get_cache_path(identifier, type) / f"{identifier}_scandata.xml"
        if not scandata_file.exists():
            scandata_file = get_scandata(identifier)
        return scandata_file

def get_namespace(element):
    m = re.match("{.*}", element.tag)
    return m.group(0) if m else ''

def parse_scandata(xml_file, identifier):
    """
    Parse scandata.xml and return list of pages that should be added to access formats.
    Returns list of tuples: (original_filename, should_add)
    """
    # Parse the XML
    root = ET.parse(xml_file)
    pages = []

    namespace = get_namespace(root.getroot())

    # Find all page elements
    for page in root.findall('.//{0}page'.format(namespace)):
        leaf_num = int(page.attrib['leafNum'])
        add_to_access = page.find('{0}addToAccessFormats'.format(namespace))

        if leaf_num is not None and add_to_access is not None: # and orig_name is not None:
            should_add = add_to_access.text.lower() == 'true'
            original_name = f"{identifier}_{leaf_num:04d}.jp2"

            pages.append({
                'leaf_num': leaf_num,
                'orig_name': original_name,
                'add_to_access': should_add
            })

    return pages

def rename_jp2_files(zip_filename, pages, dest_dir, identifier):
    """
    Read JP2 files from zip, rename sequentially based on addToAccessFormats flag.
    """
    # Filter pages that should be included
    pages_to_include = [p for p in pages if p['add_to_access']]

    # Create new zip file with renamed JP2s
    with zipfile.ZipFile(zip_filename, 'r') as input_zip:
        sequence_num = 1

        for page in pages_to_include:
            orig_name = page['orig_name']

            # Try to find the file in the zip (might have .jp2 extension)
            jp2_name = None
            for name in input_zip.namelist():
                # TODO This should really look at the sequence number
                if (str(orig_name).lower() in str(name).lower() or 
                        str(orig_name).lower().replace('.tif', '.jp2') in str(name).lower()):
                    jp2_name = name
                    break

            if jp2_name:
                # Read the file content
                file_content = input_zip.read(jp2_name)

                # Create new sequential name (4-digit padding)
                new_name = f"{identifier}_{sequence_num:04d}.jp2"

                # Write to destination with sequential name
                with open(f"{dest_dir}/{new_name}", "wb") as f:
                    f.write(file_content)

                sequence_num += 1
            else:
                logger.error(f"Could not find JP2 file for {orig_name}")
                # return None

def create_webp_files(identifier, input_dir, output_dir):
    """
    Process all JP2 images in the directory input_dir saing to output_dir
    """
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

    return(str(output_dir))

def sync_dir_to_aws_s3(source_path, pattern, bucket, prefix):
    s3_client = boto3.client('s3')
    upload_files = list(source_path.glob(pattern))

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

def normalize_images(identifier, images_file):
    # if we got anything but JP2, we convert to JP2
    # What kind of file do we have? ZIP or TAR
    if str(images_file).lower().endswith("_jp2.zip"):
        # Normalize the filename, just in case
        if os.path.basename(images_file) != f"{identifier}_jp2.zip":
            new_images_file = Path(os.path.dirname(images_file)) / f"{identifier}_jp2.zip"
            os.rename(images_file, new_images_file)
            images_file = new_images_file

    tmp_path = tempfile.mkdtemp(dir=config['general']['scratch_path'])
    if str(images_file).lower().endswith(('.zip', '.ZIP')):
        # unzip the file
        with zipfile.ZipFile(images_file, 'r') as zip_ref:
            zip_ref.extractall(tmp_path)

    if str(images_file).lower().endswith(('.tar', '.TAR')):
        # untar the file
        with tarfile.TarFile(images_file, 'r') as tar_ref:
            tar_ref.extractall(tmp_path)

    # check the extracted files to see what they are
    tmp_path = Path(tmp_path)
    image_files = list(tmp_path.glob('*.jp2')) + list(tmp_path.glob('*.JP2'))
    if not image_files:
        image_files = list(tmp_path.glob('*/*.jp2')) + list(tmp_path.glob('*/*.JP2'))

    if not image_files:
        image_files = list(tmp_path.glob('*.tif')) + list(tmp_path.glob('*.TIF'))

    if not image_files:
        image_files = list(tmp_path.glob('*/*.tif')) + list(tmp_path.glob('*/*.TIF'))

    if not image_files:
        image_files = list(tmp_path.glob('*.jpg')) + list(tmp_path.glob('*.JPG'))

    if not image_files:
        image_files = list(tmp_path.glob('*/*.jpg')) + list(tmp_path.glob('*/*.JPG'))

    jp2_tmp = tempfile.mkdtemp(dir=config['general']['scratch_path'])
    jp2_tmp_path = Path(jp2_tmp) / f"{identifier}_jp2"
    jp2_tmp_path.mkdir(parents=True, exist_ok=True)
    for input_file in image_files:
        match = re.search("_([0-9]{4})$", input_file.stem)
        seq = match.group(1)

        output_file = jp2_tmp_path / f"{identifier}_{seq}.jp2"
        if str(input_file).endswith('.jp2') or str(input_file).endswith('.JP2'):
            shutil.copy(input_file, output_file)
        else:
            img = pyvips.Image.new_from_file(input_file, access='sequential')
            img.jp2ksave(output_file)

    # zip to jp2.zip
    zip_filename = get_cache_path(identifier, 'images') / f"{identifier}_jp2.zip"
    with zipfile.ZipFile(zip_filename, 'w') as zip_ref:
        for file in jp2_tmp_path.glob('*'):
            zip_ref.write(file, file.relative_to(jp2_tmp_path.parent))

    # Cleanup
    shutil.rmtree(tmp_path)
    shutil.rmtree(jp2_tmp)

    return zip_filename

def get_ocr(identifier):
    """
    Get the OCR for an item or part and save it to our local cache
    """
    global bhl_object
    # Determine if we have an item or a part
    if bhl_object is not None:
        # we already have an object, let's be sure we have the OCR
        bhl_object.get_ocr()
    else:
        # This should never happen, but just in case, get the object from BHL
        bhl_object = BHL_Object(config, Identifier=identifier, OCR=True, Logger=logger)

    id_zfill = str(bhl_object.id).zfill(6)
    ocr_path = get_cache_path(identifier, 'ocr') / f"{bhl_object.type}-{id_zfill}"
    ocr_path.mkdir(parents=True, exist_ok=True)
    for i in range(len(bhl_object.pages)):
        page_id = str(bhl_object.pages[i]['PageID']).zfill(8)
        seq = str(i + 1).zfill(4)
        ocr_filename = ocr_path / f"{bhl_object.type}-{id_zfill}-{page_id}-{seq}.txt"
        ocr_text = ""
        if ocr_filename.exists():
            continue

        # Handle the lack of OcrText in the object, use the OcrUrl instead
        if "OcrText" in bhl_object.pages[i]:
            ocr_text = bhl_object.pages[i]['OcrText']
        else:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetPageMetadata&pageid={bhl_object.pages[i]['PageID']}&ocr=t&format=json&apikey={config['general']['bhl_api_key']}"
            logger.info(f"OCR URL /api3?op=GetPageMetadata&pageid={bhl_object.pages[i]['PageID']}&ocr=t&format=json&apikey=API_KEY")

            # TODO Update download_url() to return a data stream
            # instead of a filename to save us from reopening a file
            # that we just saved
            temp_file = download_url(url, config['general']['scratch_path'], logger, config)
            time.sleep(0.25)
            if temp_file is not None:
                with open(temp_file, 'r') as file:
                    ocr_object = json.load(file)
                    ocr_text = ocr_object['Result'][0]['OcrText']
            else:
                return None

        # Normalize line endings to CRLF
        if "\n\r" in ocr_text:
            # Old timey Mac line endings
            with open(ocr_filename, 'w') as file:
                file.write(ocr_text.replace("\n\r", "\r\n"))
        elif "\n" in ocr_text:
            # Linux line endings
            with open(ocr_filename, 'w') as file:
                file.write(ocr_text.replace("\n", "\r\n"))
        else:
            with open(ocr_filename, 'w') as file:
                file.write(ocr_text)


    return (ocr_path, f"{bhl_object.type}-{id_zfill}")

def combine_ocr(identifier, ocr_dir):
    path_parts = str(ocr_dir).split('/')
    base = path_parts[len(path_parts)-1]
    fulltext_filename = ocr_dir / f"{base}.txt"
    ocr_files = list(ocr_dir.glob('*-*-*-*.txt'))
    ocr_files.sort()
    with open(fulltext_filename, "w") as fulltext:
        for file in ocr_files:
            with open(file, "r") as ocr:
                for line in ocr:
                    fulltext.write(line)
                fulltext.write("\r\n")

    return (fulltext_filename, base)

def update_item(Identifier=None, Images=True, Scandata=True, OCR=True, StdOut=False, Verbose=False, DryRun=False, Cleanup=True):
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

    # -------------------
    # Make sure we have a valid object
    # -------------------
    if bhl_object is None:
        bhl_object = BHL_Object(config, Identifier=Identifier, Logger=logger)

    if bhl_object.object is None:
        # If it's not in BHL, we can't continue
        # TODO: Allow us to override on the CLI. We CAN continue, but we can't get OCR if it's not in BHL
        # TODO: Addendum: we can get the OCR from the DJVU/hOCR, but we'd be duplicating work.
        print(f"{Identifier} is not in BHL. Stopping.")
        logging.error('Identifier is not in BHL. Stopping.')
        sys.exit(1)

    if bhl_object.type == 'virtual_item':
        # If this is an item and it's a virtual item, we can't process it, so we check.
        print(f"{Identifier} is a virtual item. Stopping.")
        logging.error(f"{Identifier} is a virtual item. Stopping.")
        sys.exit(1)

    # ---------------
    # Handle what data to process
    # ---------------
    # If no other args were supplied, do them all
    if not Images and not Scandata and not OCR:
        Images = True
        Scandata = True
        OCR = True

    # Always send scandata with the images
    if Images:
        Scandata = True

    if DryRun:
        logger.info('Dry Run selected. Not uploading to AWS.')

    # ---------------
    # Let's goooo!
    # ---------------
    try:
        jp2_dir = None
        scandata_file = None
        jp2_file = None
        json_file = None
        if Images:
            # Download scandata.xml
            # ---------------------
            logger.info('Checking Scandata')
            scandata_file = download_file(Identifier, "scandata")
            if scandata_file is None:
                logger.error('Scandata not found.')
                sys.exit(1)

            # Download and normalize images
            # -----------------------------
            logger.info('Images Download')
            images_file = download_file(Identifier, "images")

            # guarantee we have a ZIP of JP2s
            logger.info('Images Normalize')
            jp2_file = normalize_images(Identifier, images_file)

            if jp2_file is None:
                logger.error('Images not found')
                sys.exit(1)

            # Parse scandata.xml
            # ------------------
            logger.info('Parse Scandata')
            pages = parse_scandata(scandata_file, Identifier)
            page_count = len(pages)

            # Rename JP2 files using scandata.xml
            # -----------------------------------
            logger.info(f"Images Rename (count: {page_count})")
            jp2_dir = Path(config['general']['scratch_path']) / f"{Identifier}"
            jp2_dir.mkdir(parents=True, exist_ok=True)
            rename_jp2_files(jp2_file, pages, jp2_dir, Identifier)

            # Convert to webp
            # ---------------
            logger.info('Create WebP')
            webp_dir = jp2_dir / "webp"
            webp_dir.mkdir(parents=True, exist_ok=True)
            create_webp_files(Identifier, jp2_dir, webp_dir)

            # Send the webp files to Amazon
            # -----------------------------
            if DryRun:
                logger.info('(dry run) Not uploading images to AWS')
            else:
                logger.info('Upload to AWS')
                sync_dir_to_aws_s3(jp2_dir, '*.jp2', 'bhl-open-data', f"images/{Identifier}")
                sync_dir_to_aws_s3(webp_dir, '*.webp', 'bhl-open-data', f"web/{Identifier}")
                sync_file_to_aws_s3(scandata_file, 'bhl-open-data', 'scandata')

        if Scandata and not Images:
            scandata_file = get_ia_file(Identifier, type="scandata")
            if scandata_file is None or jp2_file is None:
                logger.error(f"Scandata for {Identifier} not found on Qumulo")
            else:
                if DryRun:
                    logger.info('(dry run) Not uploading scandata to AWS')
                else:
                    sync_file_to_aws_s3(scandata_file, 'bhl-open-data', 'scandata')

        ocr_dir = None
        if OCR:
            logger.info('Getting OCR')
            (ocr_dir, key) = get_ocr(Identifier)
            (fulltext_file, key) = combine_ocr(Identifier, ocr_dir)
            if DryRun:
                logger.info('(dry run) Not uploading ocr to AWS')
            else:
                sync_dir_to_aws_s3(ocr_dir, '*.txt', 'bhl-open-data', f"ocr/{key}")

        # Cleanup
        # -------
        if Cleanup: 
            logger.info('Cleanup')
            if jp2_dir is not None:
                shutil.rmtree(jp2_dir)
            if ocr_dir is not None:
                shutil.rmtree(ocr_dir)
            if scandata_file is not None:
                os.remove(scandata_file)
            if jp2_file is not None:
                os.remove(jp2_file)
            if json_file is not None:
                os.remove(json_file)
        else:
            logger.info('Cleanup: Keeping Downloads')

    except Exception as e:
        logger.error(e)

def clean_item(identifier):
    logger.info('Cleaning Metadata and Images')
    metadata_file = get_cache_path(identifier, 'metadata') / identifier[0:1] / f"{identifier}.json"
    if metadata_file.exists():
        os.remove(str(metadata_file))

    images_file = get_cache_path(identifier, 'images') / f"{identifier}_jp2.zip"
    if images_file.exists():
        os.remove(str(images_file))

    # scandata_file = get_cache_path(identifier, 'scandata') / f"{identifier}_scandata.xml"
    # if scandata_file.exists():
    #     os.remove(str(scandata_file))

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
        '--id',
        default=None,
        required=False,
        help='BHL Item ID for the item.'
    )
    parser.add_argument(
        '--pop',
        action='store',
        default=None,
        help='Read the identifer from the first line in the file specified.'
    )
    parser.add_argument(
        '-i', '--images-only',
        action='store_true',
        help='Download JP2 images from IA, convert to WebP, send to AWS. Implies --scandata-only'
    )
    parser.add_argument(
        '-s', '--scandata-only',
        action='store_true',
        help='Download scandata.xml fom IA, send to AWS'
    )
    parser.add_argument(
        '-o', '--ocr-only',
        action='store_true',
        help='Download OCR from IA, or BHL if transcribed content, send to AWS. Implies --fulltext-only'
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help='Do not use existing files. Download all from Internet Archive.'
    )
    parser.add_argument(
        '--stdout',
        action='store_true',
        help='Output to STDOUT as well as the log file'
    )
    parser.add_argument(
        '--keep-downloads',
        action='store_false',
        help='Don\'t delete downloaded files and derivatives in cache and temp directories.'
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
    if args.identifier:
        Identifier = args.identifier
        logging.info(f"Processing {Identifier}")

    # If we got an Item ID number from the command line, use that
    if args.id:
        bhl_object = BHL_Object(config, ID=args.id, Logger=logging)
        Identifier = bhl_object.identifier
        logging.info(f"Processing {Identifier} from ID {args.id}")
    
    # If we are told to use a file as queue, pop the first  
    # row from the file and use that.
    if Identifier is None and args.pop is not None: 
        Identifier = popHead(1, args.pop)[0]

    if Identifier is None: 
        print("No identifier found or provided.")
        sys.exit(64)

    if args.clean:
        clean_item(Identifier)
    
    update_item(
        Identifier = Identifier,
        Images = args.images_only,
        Scandata = args.scandata_only,
        OCR = args.ocr_only,
        StdOut = args.stdout,
        Verbose = args.verbose,
        DryRun = args.dryrun,
        Cleanup = args.keep_downloads
    )

if __name__ == "__main__":
    main()
