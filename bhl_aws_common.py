import tempfile
import requests
import logging
import time
import re
import boto3
import xml.etree.ElementTree as ET
from pathlib import Path
from bhl_object import BHL_Object

def download_url(Url, Temp_Path, Logger=None, Config=None):
    """
    Reliably download a URL, handling 4xx and 5xx status codes
    with a cooldown and retry
    """
    # TODO Update this to return a data stream
    # TODO Update to take the destination filename instead of returning a temp file

    max_retries = 5
    retry_delay = 1

    temp_name = next(tempfile._get_candidate_names())
    temp_filepath = Path(Temp_Path) / temp_name

    header_vals = {}
    if "archive.org" in Url:
        key = Config['internet_archive']['s3_access_key']
        secret = Config['internet_archive']['s3_secret']
        header_vals['Authorization'] = f"LOW {key}{secret}"

    for attempt in range(max_retries):
        try:
            response = requests.get(Url, stream=True, headers=header_vals, timeout=15)
            if response.status_code == 200:
                bytes = 0
                with open(temp_filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        bytes = bytes + 8192
                        if bytes % 1077936128 == 0:
                            gb = int(bytes/1077936128)
                            Logger.debug(f"Downloaded {gb} GB")
                        f.write(chunk)
                return temp_filepath
            elif response.status_code == 429:
                if attempt < max_retries - 1:
                    delay = retry_delay * (attempt + 1)
                    if Logger is not None:
                        Logger.warning(f"Got HTTP {response.status_code}. Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    if Logger is not None:
                        Logger.error(f"Max Retries Reached for {Url}")
                    else: 
                        print(f"Max Retries Reached for {Url}")
                    return None
            else:
                response.raise_for_status()
        except requests.RequestException as e:
            if Logger is not None:
                Logger.error(e)
            else: 
                print(e)
            return None
        except Exception as e:
            if Logger is not None:
                Logger.info(e)
            else: 
                print(e)
            return None

def get_namespace(element):
    m = re.match("{.*}", element.tag)
    return m.group(0) if m else ''

def parse_scandata(xml_file, identifier):
    """
    Parse scandata.xml and return list of pages that should be added to access formats.
    Returns list of tuples: (original_filename, should_add)
    """
    # Parse the XML
    try:
        root = ET.parse(xml_file)
    except Exception as e:
        print(e)
        return []

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

def count_s3_items(bucket, prefix, filter=None):
    s3_session = boto3.Session('default')
    s3_client = boto3.client('s3', aws_session_token=s3_session)
    
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

def audit_item(identifier, id, config):
    ret = {
        'identifier': identifier,
        'tag': '',
        'jp2_count': 0,
        'scandata_count': 0,
        'scandata_good': '--',
        'aws_pages_count': 0,
        'ia_pages_count': 0,
        'ocr_count': 0,
        'expected_ocr': 0,
        'ocr_good': '--',
        'webp_count': 0,
        'expected_webp': 0,
        'webp_good': '--',
        'scandata_images_good': '--'
    }
    # Get the BHL Objeect
    bhl_object = BHL_Object(config, Identifier=identifier, ID=id)
    if bhl_object.object is None:
        bhl_object = BHL_Object(config, ID=identifier)
        if bhl_object.object is None:
            ret['tag'] = 'NOT FOUND'
            return(ret)
    ret['identifier'] = bhl_object.identifier

    if bhl_object.type == 'virtual_item':
        ret['tag'] = 'VIRTUAL ITEM'
        return(ret)

    id_zfill = str(bhl_object.id).zfill(6)
    ret['tag'] = f"{bhl_object.type}-{id_zfill}"

    # Get counts of the files at AWS
    ret['jp2_count']      = count_s3_items("bhl-open-data", f"images/{bhl_object.identifier}/", ".jp2")
    ret['scandata_count'] = count_s3_items("bhl-open-data", f"scandata/{bhl_object.identifier}_scandata.xml", ".xml")
    ret['ocr_count']      = count_s3_items("bhl-open-data", f"ocr/{ret['tag']}/", ".txt")
    ret['webp_count']     = count_s3_items("bhl-open-data", f"web/{bhl_object.identifier}/", ".webp")

    ret['scandata_good'] = 'OK' if (ret['scandata_count'] >= 1) else '--'
    ret['ocr_good'] = 'OK' if (ret['jp2_count'] > 0 and (ret['jp2_count'] + 1) <= ret['ocr_count']) else '--'
    ret['webp_good'] =  'OK' if (ret['jp2_count'] > 0 and (ret['jp2_count'] * 5) <= ret['webp_count']) else '--'
    ret['expected_ocr'] = ret['jp2_count'] + 1
    ret['expected_webp'] = ret['jp2_count'] * 5

    # Get and count pages in the two scandata files
    aws_scandata_url = f"https://bhl-open-data.s3.us-east-2.amazonaws.com/scandata/{bhl_object.identifier}_scandata.xml"
    ia_scandata_url = f"https://archive.org/download/{bhl_object.identifier}/{bhl_object.identifier}_scandata.xml"

    aws_scandata_temp = download_url(aws_scandata_url, config['general']['scratch_path'], None, config)
    ia_scandata_temp = download_url(ia_scandata_url, config['general']['scratch_path'], None, config)

    # Count the pages in the scandatas
    aws_pages = parse_scandata(aws_scandata_temp, {bhl_object.identifier})
    ia_pages = parse_scandata(aws_scandata_temp, {bhl_object.identifier})

    aws_pages_to_include = [p for p in aws_pages if p['add_to_access']]
    ia_pages_to_include = [p for p in ia_pages if p['add_to_access']]

    ret['scandata_images_good'] = 'OK' if (len(aws_pages_to_include) > 0 and len(ia_pages_to_include) > 0 and 
                                           len(aws_pages_to_include) == len(ia_pages_to_include)) else '--'

    ret['aws_pages_count'] = len(aws_pages_to_include)
    ret['ia_pages_count'] = len(ia_pages_to_include)
    return(ret)