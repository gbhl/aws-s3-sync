import tempfile
import requests
import logging
import time
import re
import boto3
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
            response = requests.get(Url, stream=True, headers=header_vals)
            if response.status_code == 200:
                if Logger is not None:
                    sz = -1
                    if 'Content-Length' in response.headers:
                        sz = response.headers['Content-Length']
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

def audit_item(identifier, config):

    bhl_object = BHL_Object(config, Identifier=identifier)
    if bhl_object.object is None:
        bhl_object = BHL_Object(config, ID=identifier)
        if bhl_object.object is None:
            print(f"Identifier/ID {identifier} is not in BHL. Stopping.")
            sys.exit(1)

    if bhl_object.type == 'virtual_item':
        print(f"{bhl_object.identifier} is a virtual item. Stopping.")
    id_zfill = str(bhl_object.id).zfill(6)
    tag = f"{bhl_object.type}-{id_zfill}"

    jp2_count      = count_s3_items("bhl-open-data", f"images/{bhl_object.identifier}/", ".jp2")
    scandata_count = count_s3_items("bhl-open-data", f"scandata/{bhl_object.identifier}_scandata.xml", ".xml")
    ocr_count      = count_s3_items("bhl-open-data", f"ocr/{tag}/", ".txt")
    webp_count     = count_s3_items("bhl-open-data", f"web/{bhl_object.identifier}/", ".webp")

    scandata_good = 'OK' if (scandata_count >= 1) else 'Not OK'
    ocr_good = 'OK' if (jp2_count > 0 and (jp2_count + 1) <= ocr_count) else 'Not OK'
    webp_good =  'OK' if (jp2_count > 0 and (jp2_count * 5) <= webp_count) else 'Not OK'
    expected_ocr = jp2_count + 1
    expected_webp = jp2_count * 5

    return {
        "identifier": bhl_object.identifier,
        "tag": tag,
        "jp2_count": jp2_count,
        "scandata_count": scandata_count,
        "scandata_good": scandata_good,
        "ocr_count": ocr_count,
        "expected_ocr": expected_ocr,
        "ocr_good": ocr_good,
        "webp_count": webp_count,
        "expected_webp": expected_webp,
        "webp_good": webp_good
    }
