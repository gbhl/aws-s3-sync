#!/usr/bin/env python3
"""
Monitor RabbitMQ queues and runs update-aws-item.py workers.

Polls three message queues every 60 seconds.
Runs update-aws-item.py for each message, respecting the concurrency limit.
Messages in the ocr-only queue are processed with the --ocr-only flag.
Failed workers (non-zero exit) are re-queued to the error queue if configured.
"""

import sys
import os
import subprocess
import logging
import time
import toml
import pika
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
SCRIPT = PROJECT_DIR / 'update-aws-item.py'

# Load config
config_file = PROJECT_DIR / 'config.toml'
if not config_file.exists():
    print("config.toml not found.")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = toml.load(f)

# Set up logging
log_path = PROJECT_DIR / config['logging']['path']
log_path.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=log_path / 'monitor-queue.log',
    format="%(asctime)s: %(module)s (%(levelname)s): %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# Silence other loggers
for log_name, log_obj in logging.Logger.manager.loggerDict.items():
     if log_name != __name__:
          log_obj.disabled = True

# Mirror log output to stdout so systemd journal captures it
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(logging.Formatter("%(asctime)s: %(module)s (%(levelname)s): %(message)s"))
logger.addHandler(stdout_handler)


def publish_to_error_queue(rmq_config, error_queue, identifier):
    """Publish a list of identifiers to the error queue."""
    try:
        connection = connect(rmq_config)
        channel = connection.channel()
        channel.queue_declare(queue=error_queue, durable=True)
        channel.basic_publish(
            exchange='',
            routing_key=error_queue,
            body=identifier.encode('utf-8'),
            properties=pika.BasicProperties(delivery_mode=2),
        )
        logger.info(f"Re-queued failed identifier to '{error_queue}': {identifier}")
        connection.close()
    except Exception as e:
        logger.error(f"Failed to publish {identifier} to error queue '{error_queue}': {e}")


def check_processes(processes, rmq_config=None, error_queue_suffix=None):
    """Remove finished subprocesses from the list and log their exit status.

    If rmq_config and error_queue are provided, failed workers are re-published
    to the error queue.
    """
    still_running = []
    failed = []
    for p, identifier, queue in processes:
        rc = p.poll()
        if rc is None:
            still_running.append((p, identifier, queue))
        else:
            if rc == 0:
                logger.info(f"Worker finished: {identifier}")
            else:
                logger.warning(f"Worker exited with code {rc}: {identifier}")
                if rmq_config and error_queue_suffix:
                    publish_to_error_queue(rmq_config, f"{queue}{error_queue_suffix}", identifier)

    return still_running


def connect(rmq_config):
    credentials = pika.PlainCredentials(
        rmq_config.get('username', 'guest'),
        rmq_config.get('password', 'guest'),
    )
    parameters = pika.ConnectionParameters(
        host=rmq_config['host'],
        port=int(rmq_config.get('port', 5672)),
        # TODO Do I need to use virtual host?
        virtual_host=rmq_config.get('vhost', '/'),
        credentials=credentials,
        connection_attempts=3,
        retry_delay=5,
    )
    return pika.BlockingConnection(parameters)


def read_queue(channel, queue):
    """
    Fetch one message without blocking.
    Returns (identifier, delivery_tag) or (None, None) if the queue is empty.
    """
    try:
        method, _properties, body = channel.basic_get(queue=queue, auto_ack=False)
    except Exception as e:
        logger.error(f"Error reading from queue '{queue}': {e}")
        return None, None

    if method is None:
        return None, None
    
    # TODO check this against Mike's queue data
    return body.decode('utf-8').strip(), method.delivery_tag


def start_worker(identifier, ocr_only=False, recent=False):
    cmd = [sys.executable, str(SCRIPT), '--identifier', identifier]
    if ocr_only:
        cmd.append('--ocr-only')
    if recent:
        cmd.append('--ia-recent')
    logger.info(f"Spawning: {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=PROJECT_DIR)


def read_queues(rmq_config, queues, slots):
    """
    Open a connection, pull up to `slots` messages across both queues, and
    return a list of spawned subprocesses.  New-items queue is preferred over 
    ocr_items and updated-items.
    """
    new_queue = queues['new_items']
    updated_queue = queues['updated_items']
    ocr_queue = queues['ocr_only']
    spawned = []

    try:
        connection = connect(rmq_config)
        channel = connection.channel()

        for queue, ocr_only, recent in [(new_queue, False, False), (ocr_queue, True, False), (updated_queue, False, True)]:
            while len(spawned) < slots:
                if queue == "":
                    continue
                message, tag = read_queue(channel, queue)
                if message is None:
                    break
                msg_parts = message.split("|")
                identifier = msg_parts[2]
                proc = start_worker(identifier, ocr_only=ocr_only, recent=recent)
                channel.basic_ack(delivery_tag=tag)
                spawned.append((proc, identifier, queue))

        connection.close()
    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"Could not connect to RabbitMQ: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while polling queues: {e}")

    return spawned


def main():
    rmq = config['rabbitmq']
    queues = config['queues']
    concurrency = int(rmq.get('concurrency', 1))
    error_queue_suffix = queues.get('error_queue_suffix', '').strip() or None

    logger.info(f"Starting monitor-queue (concurrency={concurrency})")
    logger.info(f"New Items: '{queues['new_items']}'  | Updates Items: '{queues['updated_items']}' | OCR queue: '{queues['ocr_only']}'")
    if error_queue_suffix:
        logger.info(f"Error queue suffix: '{error_queue_suffix}'")
    else:
        logger.warning("No error queue suffix configured — failed workers will only be logged")

    processes = []

    while True:
        processes = check_processes(processes, rmq_config=rmq, error_queue_suffix=error_queue_suffix)
        slots = concurrency - len(processes)

        if slots > 0:
            new_procs = read_queues(rmq, queues, slots)
            processes.extend(new_procs)

        time.sleep(config['rabbitmq']['poll_interval'])


if __name__ == '__main__':
    main()
