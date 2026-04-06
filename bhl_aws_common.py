import tempfile
import requests
import logging
from pathlib import Path

def download_url(Url, Temp_Path, Logger=None):
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
                    if Logger is not None:
                        Logger.warning(f"Got HTTP {response.status_code}. Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    if Logger is not None:
                        Logger.error(f"Max Retries Reached for {Url}")
                    return None
            else:
                response.raise_for_status()
        except requests.RequestException as e:
            if Logger is not None:
                Logger.error(e)
            return None
        except Exception as e:
            if Logger is not None:
                Logger.info(e)
            return None

