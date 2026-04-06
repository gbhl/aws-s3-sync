# bhl_aws_common.py

import tempfile
import requests
import json
import os
from pathlib import Path
from bhl_aws_common import download_url

class BHL_Object:
    def __init__(self, Config, Identifier=None, ID=None, OCR=False):
        self.identifier = Identifier
        self.id = ID # Item ID, never Part ID
        self.ocr = OCR
        self.pages = None
        self.object = None
        self.type = None
        self.api_key = Config['general']['bhl_api_key']
        self.scratch_path = Config['general']['scratch_path']

        # If this is an item and it's a virtual item, we can't process it, so we check.
        self.get_bhl_item()

        if self.object is None and self.identifier is not None:
            # Check for a part only if we are using the Identiifer
            # self.id is assumed to be Item ID, not Part ID
            self.get_bhl_part()

    def get_bhl_item(self):
        """
        Get the BHL metadata for an item. Uses either ID number or IA identifier.
        Returns the type of the object ("item" or "virtual item"), ID Number and the Object
        """
        ocr = 't' if self.ocr else 'f'

        url = None
        if self.identifier is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={self.identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
        elif self.id is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={self.id}&idtype=bhl&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
        else:
            return

        temp_file = download_url(url, self.scratch_path)

        # Let's hope we always get some data
        if temp_file is None:
            return

        # read and process the JSON data
        with open(temp_file, 'r') as file:
            data = json.load(file)

        os.remove(temp_file) # Don't need the file anymore
        if len(data['Result']) == 1:
            self.object = data['Result'][0]
            self.id = self.object['ItemID']
            if 'SourceIdentifier' in self.object:
                self.identifier = self.object['SourceIdentifier']
            elif 'BarCode' in self.object:
                self.identifier = self.object['BarCode']

            self.pages = self.object['Pages']

            if self.object['Source'] == "Virtual Item":
                self.type = 'virtual_item'
            else:
                self.type = 'item'

    def get_bhl_part(self):
        ocr = 't' if self.ocr else 'f'

        url = None
        if self.identifier is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetPartMetadata&id={self.identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
        elif self.id is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetPartMetadata&id={self.id}&idtype=bhl&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
        else:
            return

        temp_file = download_url(url, self.scratch_path)

        if temp_file is None:
            return

        # read and process the JSON data
        with open(temp_file, 'r') as file:
            data = json.load(file)

        os.remove(temp_file) # Don't need the file anymore
        if len(data['Result']) == 1:
            self.type = 'part'
            self.object = data['Result'][0]
            self.id = self.object['PartID']
            self.identifier = self.object['BarCode']
            self.pages = self.object['Pages']

    def get_ocr(self):
        """
        Calls BHL again to get the OCR for an item if we haven't already
        gotten the OCR from BHL. This has no effect for parts. (for now)
        """
        if self.ocr:
            # we already got the OCR
            return True

        # Call BHL again to get the OCR
        self.ocr = True
        if self.type == 'item':
            self.get_bhl_item()
            return True
        elif self.type == 'part':
            return True
        else:
            return False




