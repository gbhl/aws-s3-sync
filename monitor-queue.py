#!/usr/bin/env python3
"""
Monitor RabbitMQ queues and runs update-aws-item.py workers.

Polls full-items-queue and ocr-only-queue every 60 seconds.
Runs update-aws-item.py for each message, respecting the concurrency limit.
Messages in ocr-only-queue are processed with the --ocr-only flag.
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
POLL_INTERVAL = 60  # seconds

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
logger = logging.getLogger('monitor-queue')

# Mirror log output to stdout so systemd journal captures it
stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setFormatter(logging.Formatter("%(asctime)s: %(module)s (%(levelname)s): %(message)s"))
logger.addHandler(stdout_handler)


def check_processes(processes):
    """Remove finished subprocesses from the list and log their exit status."""
    still_running = []
    for p in processes:
        rc = p.poll()
        if rc is None:
            still_running.append(p)
        else:
            identifier = '?'
            try:
                idx = p.args.index('--identifier')
                identifier = p.args[idx + 1]
            except (ValueError, IndexError):
                pass
            if rc == 0:
                logger.info(f"Worker finished: {identifier}")
            else:
                logger.warning(f"Worker exited with code {rc}: {identifier}")
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


def start_worker(identifier, ocr_only=False):
    cmd = [sys.executable, str(SCRIPT), '--identifier', identifier]
    if ocr_only:
        cmd.append('--ocr-only')
    logger.info(f"Spawning: {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=PROJECT_DIR)


def read_queues(rmq_config, slots):
    """
    Open a connection, pull up to `slots` messages across both queues, and
    return a list of spawned subprocesses.  Full-items queue is preferred.
    """
    full_queue = rmq_config['full-items-queue']
    ocr_queue = rmq_config['ocr-only-queue']
    spawned = []

    try:
        connection = connect(rmq_config)
        channel = connection.channel()

        for queue, ocr_only in [(full_queue, False), (ocr_queue, True)]:
            while len(spawned) < slots:
                message, tag = read_queue(channel, queue)
                if message is None:
                    break
                msg_parts = message.split("|")
                identifier = msg_parts[2]
                proc = start_worker(identifier, ocr_only=ocr_only)
                channel.basic_ack(delivery_tag=tag)
                spawned.append(proc)

        connection.close()
    except pika.exceptions.AMQPConnectionError as e:
        logger.error(f"Could not connect to RabbitMQ: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while polling queues: {e}")

    return spawned


def main():
    rmq = config['rabbitmq']
    concurrency = int(rmq.get('concurrency', 1))

    logger.info(f"Starting monitor-queue (concurrency={concurrency})")
    logger.info(f"Full queue: '{rmq['full-items-queue']}'  |  OCR queue: '{rmq['ocr-only-queue']}'")

    processes = []

    while True:
        processes = check_processes(processes)
        slots = concurrency - len(processes)

        if slots > 0:
            new_procs = read_queues(rmq, slots)
            processes.extend(new_procs)

        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
