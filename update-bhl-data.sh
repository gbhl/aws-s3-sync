#!/bin/bash

# This will update the BHL TSV Data Dumps at AWS
# Should be run monthly

# Download from BHL
mkdir -p bhl-data-temp
cd bhl-data-temp
wget https://www.biodiversitylibrary.org/Data/TSV/hosted/data.zip

# Convert to gzip
unzip data.zip
cd Data
gzip *

# Update last modified
echo "BHL Open Data" > last-updated.txt
echo -n "Last Updated: " >> last-updated.txt
date >> last-updated.txt

# Update at AWS and cleanup
aws s3 sync . s3://bhl-open-data/data/
cd ../..
rm -fr bhl-data-temp