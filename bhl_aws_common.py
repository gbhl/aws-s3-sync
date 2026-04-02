# bhl_aws_common.py

import tempfile
import requests
import json
import logging
import os
from pathlib import Path

def download_url(Config, Url):
    """
    Reliably download a URL, handling 4xx and 5xx status codes
    with a cooldown and retry
    """
    # TODO Update this to return a data stream
    # TODO Update to take the destination filename instead of returning a temp file

    max_retries = 5
    retry_delay = 1

    temp_name = next(tempfile._get_candidate_names())
    temp_filepath = Path(Config['general']['scratch_path']) / temp_name

    for attempt in range(max_retries):
        try:
            response = requests.get(Url)
            if response.status_code == 200:
                with open(temp_filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=128):
                        f.write(chunk)
                return temp_filepath
            elif response.status_code == 429:
                if attempt < max_retries - 1:
                    delay = retry_delay * (attempt + 1)
                    logger.warning(f"Got HTTP {response.status_code}. Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    logger.error(f"Max Retries Reached for {Url}")
                    return None
            else:
                response.raise_for_status()
        except requests.RequestException as e:
            logger.error(e)
            return None
        except Exception as e:
            logger.info(e)
            return None

def get_bhl_item(Config, Identifier=None, ID=None, OCR=False):
    """
    Get the BHL metadata for an item. Uses either ID number or IA identifier.
    Returns the type of the object ("item" or "virtual item"), ID Number and the Object
    """
    ocr = 't'
    if not OCR:
        ocr = 'f'

    url = None
    api_key = Config['general']['bhl_api_key']
    if Identifier is not None:
        url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={Identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={api_key}"
    elif ID is not None:
        url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={ID}&idtype=bhl&pages=t&ocr={ocr}&format=json&apikey={api_key}"
    else:
        return (None, None, None)

    temp_file = download_url(Config, url)

    # Let's hope we always get some data
    if temp_file is None:
        return (None, None, None)

    # read and process the JSON data
    with open(temp_file, 'r') as file:
        data = json.load(file)

    os.remove(temp_file) # Don't need the file anymore
    if len(data['Result']) == 1:
        itm = data['Result'][0]
        if itm['Source'] == "Virtual Item":
            return ('virtual_item', itm['ItemID'], itm)
        else:
            return ('item', itm['ItemID'], itm)

    return (None, None, None)

def get_bhl_part(Config, Identifier=None, ID=None, OCR=False):
    ocr = 't'
    if not OCR:
        ocr = 'f'

    url = None
    api_key = Config['general']['bhl_api_key']
    if Identifier is not None:
        url = f"https://www.biodiversitylibrary.org/api3?op=GetPartMetadata&id={Identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={api_key}"
    elif ID is not None:
        url = f"https://www.biodiversitylibrary.org/api3?op=GetPartMetadata&id={ID}&idtype=bhl&pages=t&ocr={ocr}&format=json&apikey={api_key}"
    else:
        return (None, None, None)

    temp_file = download_url(Config, url)

    if temp_file is None:
        return (None, None, None)

    # read and process the JSON data
    with open(temp_file, 'r') as file:
        data = json.load(file)

    os.remove(temp_file) # Don't need the file anymore
    if len(data['Result']) == 1:
        itm = data['Result'][0]
        return ('part', itm['PartID'], itm)

    return (None, None, None)



