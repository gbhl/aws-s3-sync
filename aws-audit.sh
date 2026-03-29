#!/bin/bash
ID=$1
BUCKET="bhl-open-data"

# We need the ID from BHL
mkdir -p cache/data
# Get the item.txt and part.txt if we don't already have them
if [ ! -f "cache/data/item.txt" ]; then
    echo "Downloading item.txt"
    wget -q -O cache/data/item.txt https://www.biodiversitylibrary.org/Data/TSV/hosted/item.txt
fi

if [ ! -f "cache/data/part.txt" ]; then
    echo "Downloading part.txt"
    wget -q -O cache/data/part.txt https://www.biodiversitylibrary.org/Data/TSV/hosted/part.txt
fi

# Find the itemid or partid
ITEM_ID=$(grep $ID cache/data/item.txt | cut -f 1)
PART_ID=$(grep $ID cache/data/part.txt | cut -f 1)

if [ "$ITEM_ID" != "" ]; then
    ITEM_ID=$(printf '%06d' $ITEM_ID)
    TAG=$(echo "item-$ITEM_ID")
elif [ "$PART_ID" != ""  ]; then
    PART_ID=$(printf '%06d' $PART_ID)
    TAG=$(echo "part-$PART_ID")
fi

JP2_COUNT=$(aws s3 ls s3://$BUCKET/images/$ID/ | wc -l)
SCANDATA_COUNT=$(aws s3 ls s3://$BUCKET/scandata/${ID}_scandata.xml | wc -l)
OCR_COUNT=$(aws s3 ls s3://$BUCKET/ocr/$TAG/ | wc -l)
WEBP_COUNT=$(aws s3 ls s3://$BUCKET/web/$ID/ | wc -l)

echo "Item Summary:          $ID"
echo "JP2 Files:             $JP2_COUNT"
echo "Scandata:  (1)         $SCANDATA_COUNT"
echo "OCR Files  (JP2 + 1):  $OCR_COUNT"
echo "WEBP Files (JP2 * 5):  $WEBP_COUNT"