<?php

require 'vendor/autoload.php';

use Aws\S3\S3Client;
$bucket = 'bhl-open-data';

$client = new S3Client(['region' => 'us-east-2']);

# https://docs.aws.amazon.com/aws-sdk-php/v3/api/api-s3-2006-03-01.html#listobjectsv2

$results = $client->listObjectsV2([
	'Bucket' => $bucket,
	'MaxKeys' => 3,
	'Prefix' => 'ocr/105022',
	'MaxKeys' => 3000
]);
print_r($results);

