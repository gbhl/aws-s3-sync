# BHL Update AWS Item

Updates an item from the Biodiversity Heritage Library (BHL) at AWS S3.

BHL maintains a copy of its data at Amazon S3 as part of it's membership in the AWS Open Data Sponsorship Program. This script is meant to update an item's data at AWS to keep it in sync with BHL's metadata.

Usage of this script relies on access to both BHL's API and permission to BHL's content at AWS S3. Data is drawn from the both Internet Archive and BHL.

The script takes into account inconsistencies at the Internet Archive as much as possible to normalize the data being sent to AWS. No changes are made to BHL itself when this script is running.

## Basic Usage

```
python update-aws-item.py [--identifier IDENTIFIER]
```
The script will check for any cached files and update these types of files at AWS:

* JPEG-2000 Page Images
* Web-ready Webp Images (5 versions: full-size, large, medium, small and thumbnail)
* OCR Files (One text file per page)
* Combined OCR (All OCR files combined into one text file)
* IA Scandata (page-level metadata)

```
python audit-aws.py IDENTIFIER
```
This will give a quick summary of the counts of items at AWS and compare to the expected counts of files according to the scandata. Returns "OK"/"Not OK". 


## Advanced Usage

To process a list of items, this command may be useful:

`while read -r $ID; do python update-aws-item.py --identifier $ID; done < LIST.TXT` 

The Linux `parallel` command may be used together with the same list of identifers to process multiple items at the same time. 

This will run eight (8) copies of the script until all items are processed. The `./logs/` 
folder should be monitored for progress and output.

`cat LIST.TXT | parallel -j 8 --delay 1s python update-aws-item.py --identifier`

## Installation

This uses a virtual environment and requires Python 3.12 or greater and libvips for image manipulation.

```
python -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

### Optional installation

#### Queue Monitor systemd service

1. Edit the `bhl-aws-sync.service` and replace `/PATH/TO/INSTALL` with the installation path.
2. Then copy it to to `/etc/systemd/system`
3. Run `systemctl daemon-reload`
4. Run `systemctl start bhl-aws-sync.service`
5. Run `systemctl enable bhl-aws-sync.service`

#### Log rotation

Log rotation expects the Queue Qonitor systemd service to be installed. 

1. Edit the `logrotate.txt` file and replace `/PATH/TO/INSTALL` with the installation path.
2. Edit the `logrotate.txt` file and replace `USER` and `GROUP` with the correct owner/group of the logs folder.
3. Copy and rename it to `/etc/logrotate.d/bhl-aws-sync`.
4. Test with `logrotate -d /etc/logrotate.d/bhl-aws-sync`


## Options
`--identifier IDENTIFIER`  
Archive.org identifier for the item.

`--pop FILENAME`  
FILENAME is a list of identifiers. Reads and removes the first line of the file and uses it as the identifier to be processed.

`-i`, `--images-only`  
Download JP2 images from IA, convert to WebP, send to AWS. This will prefer the locally cached IDENTIFIER_jp2.zip. Implies `--scandata-only`

`-o`, `--ocr-only`  
Downloads OCR file for each page from BHL ansend to AWS. Also uploads ne file of all combined OCR.

`--clean`  
Removes files from the local cache. Does not remove `scandata.xml`. Downloads all other files from the Internet Archive as needed.

`--stdout`  
Outputs progress to STDOUT instead of the log file.

`--keep-downloads`  
Does not delete files downloaded to the cache and temp directories.

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

8. Cleans up any temporary files (extracted JP2 and WEBP) that were created during processing. IA Item metadata, Scandata, JP2s and OCR files will remain in the local cache.

## Error handling

Most errors are fatal and will halt processing. Temporary files created will not be deleted when this happens.

## Config File

The script needs certain values.

* WEBP Quality  (default=50)
* Cache Path (default=./cache)
    * Cache path will contain several subfoldes for JP2, JSON metadata, OCR, and Scandata.
* Temporary Working Space (default=./tmp)
* BHL API Key (no default)
* WebP Name and Sizes which should not be changed
* Log path and filename

When running as a daemon on Linux
* RabbitMQ message queue connection info
* Names of the three queues to monitor: new, updaed, ocr-only

## systemd Daemon

The `monitor-queue.py` script is started from systemd. Every minute, it checks the message queues defined in the `[queues]` section of the `config.toml` file. 

The queues contains type, ID, and identifiers of items that need updating.

When a new message appears on a queue, an instance of `update-aws-item.py` is called to process the identifier with the `--ocr-only` option applied for messages in the `ocr-only`. 

The `concurrency` setting in the config file controls how many copies of `update-aws-item.py` can be running at the same time.


## Auditing

The `audit-aws.py` script returns counts of files at AWS to compare to what is expected. A quick look indicates if an item needs to be uploaded or refreshed at AWS.

Example:

```
Item Summary:  mobot31753002623988 (item-128881)
JP2 Files:     499
-------------- Actual / Expected / (OK/Not-OK)
Scandata:      2/1 (OK)
OCR Files:     500/500 (OK)
WEBP Files:    2495/2495 (OK)
```

## Known bugs

* When OCR changes from a page realignment or insertion, existing OCR is not deleted at AWS and new OCR is uploaded. There are differences in the old and new filenames and the old are left on AWS. This is a bit wasteful and also causes a false positive error in the audit script, but the script is configured to allow this and report an OK status. Example:

```
Summary:       notessurlledel00mail (item-048997)
  JP2 Files:   654
  ------------ Actual / Expected / (OK/Not-OK)
  Scandata:    1/1 (OK)
  OCR Files:   722/655 (OK)   <---- Actual is greater than Expected
  WEBP Files:  3270/3270 (OK)
```

## Notes

### Scandata File

As much as possible, this script will preserve the locally cached `scandata.xml` file to prevent it from deviating from the one used by BHL. There are no provisions in the script to remove the scandata file and re-download from the Internet Archive. 

Therefore, it is important to begin using this script with a full collection of scandata.xml files for all items in BHL.

### OCR File organization

_This section is a reference for those interestd in accessing the OCR content at AWS S3._

Since there is a mixed relationship between Items and Parts/Segments, OCR files must be accessed in different ways.

1. An Item may have no Parts at all
2. An Item may hafve Parts defined within it
3. A Part may be its own Item.

#### An item with no Parts

An Item that has a `BarCode` in [item.txt](https://www.biodiversitylibrary.org/Data/TSV/hosted/) will have an image an an associated OCR file. Example:

* **Item ID:** 346951 (https://www.biodiversitylibrary.org/item/346951)  
* **BarCode:** CAT109916943238207426  
* **Page ID:** 65077706 (https://www.biodiversitylibrary.org/page/65077706)  
* **Item Sequence:** 14

AWS URLs:

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/CAT109916943238207426/CAT109916943238207426_0014.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-346951/item-346951-65077706-0014.txt

#### An Item that has Parts defined within it

Not all Parts have OCR. If a part does not have a `BarCode` in [part.txt](https://www.biodiversitylibrary.org/Data/TSV/hosted/), it's parent Item will have a `BarCode` in `item.txt` that is used to get the OCR prefixed with `item`. Example:

* **Item ID:** 21356 (https://www.biodiversitylibrary.org/item/21356#page/127/mode/1up)  
* **Part ID:** 248 (https://www.biodiversitylibrary.org/part/248)  
* **BarCode:** journalofhymenop12n2inte  
* **Page ID:** 2839616 (https://www.biodiversitylibrary.org/page/2839616)  
* **Item Sequence:** 127  
* **Part Sequence:** 1

AWS URLs (note **item-021356** vs **part-000248**):

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/journalofhymenop12n2inte/journalofhymenop12n2inte_0127.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-021356/item-021356-02839616-0127.txt
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/part-000248/part-000248-02839616-0001.txt **<< Does not exist**

#### A Part that is its own Item

Some Parts do not have parent items. If an Part has a `BarCode` in [part.txt](https://www.biodiversitylibrary.org/Data/TSV/hosted/) then it will have a correspoding OCR file prefixed with `part`. There is no corresponding OCR for the Item. In [item.txt](https://www.biodiversitylibrary.org/Data/TSV/hosted/) these will have a BarCode that looks like `vi210914v100201120250316010124` (possible regex: `/^vi\d{6}v/`)

* **Item ID:** 336513 (https://www.biodiversitylibrary.org/itemdetails/336513)  
* **Part ID:** 98691 (https://www.biodiversitylibrary.org/part/98691)  
* **BarCode:** giantresinbeema1hino  
* **Page ID:** 64253797 (https://www.biodiversitylibrary.org/page/64253797)  
* **Item Sequence:** N/A  
* **Part Sequence:** 1

AWS URLs (note **item-336513** vs **part-098691**):

* https://bhl-open-data.s3.us-east-2.amazonaws.com/images/giantresinbeema1hino/giantresinbeema1hino_0001.jp2
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/item-336513/item-336513-064253797-0001.txt **<< Does not exist**
* https://bhl-open-data.s3.us-east-2.amazonaws.com/ocr/part-098691/part-098691-064253797-0001.txt 





