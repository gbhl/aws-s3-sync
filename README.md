# BHL Update AWS Item

Updates an item from BHL at AWS S3

## Basic Usage

```
python -m venv venv  
. venv/bin/activate
pip install -r requirements.txt
python update-aws-item.py [--identifier IDENTIFIER]
```
The script will check for any cached files and update the item at AWS. File uploaded are: (#### is sequence number)

* JPEG-2000 Page Images: `IDENTIFIER_####.jp2`
* Web-ready WEBP Images: `IDENTIFIER_####.webp`
    * `IDENTIFIER_####_full.webp`
    * `IDENTIFIER_####_large.webp`
    * `IDENTIFIER_####_medium.webp`
    * `IDENTIFIER_####_small.webp`
    * `IDENTIFIER_####_thumb.webp`
* OCR Files: `IDENTIFIER_####.txt`
* Combined OCR: `IDENTIFIER.txt`
* IA Scandata: `IDENTIFIER_scandata.xml`

## Options
`--identifier IDENTIFIER`  
Archive.org identifier for the item.

`--pop FILENAME`  
FILENAME is a list of identifiers. This option reads and removes the first line of the file and uses it as the identifier to be processed.

`-i`, `--images-only`  
Download JP2 images from IA, convert to WebP, send to AWS. This will prefer the locally cached IDENTIFIER_jp2.zip. Implies `--scandata-only`

`-o`, `--ocr-only`  
Downloads OCR file for each page from BHL ansend to AWS. Implies `--fulltext-only`

`-f`, `--fulltext-only`  
Combines all OCR for the item one text file and uploads to AWS.

`--clean`  
Removes files from the local cache. Does not remove scandata.xml. Download all others from the Internet Archive as needed.

`--stdout`  
Outputs progress to STDOUT instead of the log file.

`--verbose`  
Output many more details of progress.

`-h`, `--help`   
Show a summary of the command line options.

## The Process

The script performs the following steps. Some steps will be skipped or performed based on the `-i`, `-s`, `-o`, and `-f` options described above. If none of these options are supplied, the script will do all of them.

1. Determine the identifier either from the command line or from using the file identified by the `--pop` option.

2. Using the BHL API, decide if the item is a "Virtual Item". A Virutal item does not exist as other items (either full Items or Virtual Item Segments) and does not apply to this process. 

3. If the `--clean` option was given, delete from the cache the `IDENTIFIER_jp2.zip` and the metadata downloaded from the Internet Archive.

4. If it does not already exist in the cache, download and noramalize the scandata file from the Internet Archive. If the item has a `scandata.zip`, extract and rename the XML file to `IDENTIFIER_scandata.xml`

5. If Images are to be uploaded (`-i` or `--images-only` option):

    1. Download and Normalize the images. If the `IDENTIFIER_jp2.zip` does not already exist in the cache, determine the image archive file at the Internet Archive. This is in order of preference: `jp2.zip`/`jp2.tar` or `tiff.zip`/`tiff.tar`. Other images archives are not supported. If TIFFs or a TAR file are received, the images are converted to JP2 or ZIPped as appropriate to create the `IDENTIFIER_jp2.zip`.

    2. Using the Scandata, renumber the JP2 images according to the `<addToAccessFormats>` tags. Files that have a `false` value will not be included. The resulting sequence numbers will be sequential with no gaps.

    3. Convert the JP2 files to WEBP files and resize to five WEBP files for each JP2 file. 

        * "full": 100% wide (no resizing)
        * "large": 930 px wide 
        * "medium": 465 px wide
        * "small": 235 px wide
        * "thumb": 150 px wide

    4. Upload all files to AWS: JP2, WEBP, Scandata. (it is assumed that the scandata was not re-downloaded if it already existed.)

6. If the Scandata is to be uploaded (`-s` or `--scandata-only` option):

    This runs only if Images are not already being uploaded. 

    1. If the scandata is not in the cache. Download the scandata from the Internet Archive and upload it to AWS.

7. If the OCR is to be uploaded (`-o` or `--ocr-only` option):

    1. Download the OCR from BHL (not from IA) and separate files, one per page, in parallel with the JP2 files. Note: If the item is an Item in BHL, then all OCR can be downloaded in one API call. If the item is a Part/Segment, then the OCR is downloaded one page at a time. This may hit rate limits when running in parallel.

    2. Upload the OCR files to AWS.

8. If the combined scandata is to be uploaded (`-f` or `--fulltext-only` option):

    1. If the OCR is not downloaded and processed fromA IA. Do so as described in Step 6 above. 
    
    2. Concatenate the OCR files into one .txt file and upload to AWS.

9. Cleans up any temporary files (extracted JP2 and WEBP) that were created during processing. IA Item metadata, Scandata, JP2s and OCR files will remain in the local cache.

## Error handling

Most errors are fatal and will halt processing. Temporary files created will not be deleted when this happens.

## Config File

The script needs certain values.

* WEBP Quality (deftault=50)
* Cache Path (default=./cache)
    * Cache path will contain several subfoldes for JP2, JSON metadata, OCR, and Scandata.
* Temporary Working Space (default=./tmp)
* BHL API Key (no default)
* User-Agent to attempt to avoid Cloudflare blocking (default=A recent version of Firefox)

## Notes

### Scandata File
As much as possible, this script will preserve the locally cached scandata.xml file to prevent it from deviating from the one used by BHL. There are no provisions in the script to remove the scandata file and re-download from the Internet Archive. 

### OCR File organization

Since there is a mixed relationship between Items and Parts, OCR files must be accessed in different ways.

1. An Item may have no Parts at all
2. An Item may hafve Parts defined within it
3. A Part may be its own Item.

#### An item with no Parts

An Item that has a `BarCode` in `item.txt` will have an image an an associated OCR file. Example:

* **Item ID:** 346951 (https://www.biodiversitylibrary.org/item/346951)  
* **BarCode:** CAT109916943238207426  
* **Page ID:** 65077706 (https://www.biodiversitylibrary.org/page/65077706)  
* **Item Sequence:** 14

AWS URLs:

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/CAT109916943238207426/CAT109916943238207426_0014.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-346951/item-346951-65077706-0014.txt

#### An Item that has Parts defined within it

Not all Parts have OCR. If a part does not have a `BarCode` in `part.txt`, it's parent Item will have a `BarCode` in `item.txt` 
that is used to get the OCR prefixed with `item`. Example:

* **Item ID:** 21356 (https://www.biodiversitylibrary.org/item/21356#page/127/mode/1up)  
* **Part ID:** 248 (https://www.biodiversitylibrary.org/part/248)  
* **BarCode:** journalofhymenop12n2inte  
* **Page ID:** 2839616 (https://www.biodiversitylibrary.org/page/2839616)  
* **Item Sequence:** 127  
* **Part Sequence:** 1

AWS URLs:

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/journalofhymenop12n2inte/journalofhymenop12n2inte_0127.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-021356/item-021356-02839616-0127.txt
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/part-000248/part-000248-02839616-0001.txt <-- Does not exist

#### A Part that is its own Item

Some Parts do not have parent items. If an part has a `BarCode` in `part.txt` then it will have a correspoding OCR file prefixed with `part`. There is no corresponding OCR for the Item. In `item.txt` these will have a BarCode that looks like `vi210914v100201120250316010124` (regex `/^vi\d{6}v/`)

* **Item ID:** 336513 (https://www.biodiversitylibrary.org/itemdetails/336513)  
* **Part ID:** 98691 (https://www.biodiversitylibrary.org/part/98691)  
* **BarCode:** giantresinbeema1hino  
* **Page ID:** 64253797 (https://www.biodiversitylibrary.org/page/64253797)  
* **Item Sequence:** N/A  
* **Part Sequence:** 1

AWS URLs:

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/giantresinbeema1hino/giantresinbeema1hino_0001.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-336513/item-336513-064253797-0001.txt <-- Does not exist
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/part-098691/part-098691-064253797-0001.txt 

**Mismatches between BHL and IA**

Example: 

* https://www.biodiversitylibrary.org/item/202277#page/305/mode/thumb (336 pages)
* https://archive.org/details/wildflowersofbri02adam/page/n342/mode/1up (442 pages)

Need to find and fix all of these at BHL. Could be as many as 3,000 items.



