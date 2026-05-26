# bhl_aws_common.py

import tempfile
import requests
import json
import os
from pathlib import Path
import bhl_aws_common

class BHL_Object:
    def __init__(self, Config, Identifier=None, ID=None, OCR=False, Logger=None):
        self.identifier = Identifier
        self.id = ID # Item ID, never Part ID
        self.ocr = OCR
        self.pages = None
        self.object = None
        self.type = None
        self.api_key = Config['general']['bhl_api_key']
        self.scratch_path = Config['general']['scratch_path']
        self.logger = Logger

        # If this is an item and it's a virtual item, we can't process it, so we check.
        self.get_bhl_item()

        if self.object is None and self.identifier is not None:
            self.get_bhl_part()

    def read_item_api(self, data_file):
        if data_file is None:
            return(False)

        with open(data_file, 'r') as file:
            data = json.load(file)

        if len(data['Result']) == 1:
            self.object = data['Result'][0]
            self.id = self.object['ItemID']
            if 'SourceIdentifier' in self.object: 
                # Do we need this? Should always be BarCode for Items
                self.identifier = self.object['SourceIdentifier']
            elif 'BarCode' in self.object:
                self.identifier = self.object['BarCode']

            self.pages = self.object['Pages']

            if self.object['Source'] == "Virtual Item":
                self.type = 'virtual_item'
            else:
                self.type = 'item'
            return(True)
        
        # will return False if we found more than 1 result. 
        # This should never happen.
        return(False)

    def get_bhl_item(self):
        """
        Get the BHL metadata for an item. Uses either IA identifier or ID number.
        Fills self with the type of the object ("item" or "virtual item"), ID Number and the Object
        Returns True when an item is found, False otherwise
        """
        ocr = 't' if self.ocr else 'f'

        url = None
        if self.identifier is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={self.identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
            temp_file = bhl_aws_common.download_url(url, self.scratch_path, Logger=self.logger)
            result = self.read_item_api(temp_file)
            os.remove(temp_file)
            if result:
                return(True)
                
        if self.id is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetItemMetadata&id={self.id}&idtype=bhl&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
            temp_file = bhl_aws_common.download_url(url, self.scratch_path, Logger=self.logger)
            result = self.read_item_api(temp_file)
            os.remove(temp_file)
            if result:
                return(True)

        return(False)

    def get_bhl_part(self):
        """
        Get the BHL metadata for an part. Uses only the IA identifier.
        Fills self with the type of the object ("part"), ID Number and the Object
        Returns True when a part is found, False otherwise
        """
        ocr = 't' if self.ocr else 'f'

        url = None
        # Check for a part only if we are using the Identiifer
        # self.id is assumed to be Item ID, not Part ID
        if self.identifier is not None:
            url = f"https://www.biodiversitylibrary.org/api3?op=GetPartMetadata&id={self.identifier}&idtype=ia&pages=t&ocr={ocr}&format=json&apikey={self.api_key}"
        else:
            return(False)

        temp_file = bhl_aws_common.download_url(url, self.scratch_path, Logger=self.logger)

        if temp_file is None:
            return(False)

        # read and process the JSON data
        with open(temp_file, 'r') as file:
            data = json.load(file)

        os.remove(temp_file) # Don't need the file anymore
        if len(data['Result']) == 1:
            self.type = 'part'
            self.object = data['Result'][0]
            self.id = self.object['PartID']
            self.identifier = self.object['SourceIdentifier']
            self.pages = self.object['Pages']
            return(True)

        return(False)

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




