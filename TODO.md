# TODO

Things that still need to be done. Probably.

## Update to use AWS's Scandata

Right now, the script relies on a local copy of the `scandata.xml` file from BHL's copy. We only want ot use IA's copy if it doesn't exist.

Now that all of the scandata are at AWS, update the scirpt to prefer the AWS version of the `scandata.xml` and only upload one time from IA to AWS if it doesn't exist in AWS.


## Mismatches between BHL and IA

We will need to find and correct all of these, but this is a slow process. By taking ownership of the scandata.xml files and the images at AWS S3, we mitigate this problem going forward.

The estimated number of items affected in this way is currently unknown, but may be around 1000 items.

Example: 

* https://www.biodiversitylibrary.org/item/202277#page/305/mode/thumb (336 pages)
* https://archive.org/details/wildflowersofbri02adam/page/n342/mode/1up (344 pages)
* AWS S3 contains 344 pages



