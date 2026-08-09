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
from datetime import datetime, timezone, timedelta

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


def publish_to_queue(rmq_config, queue, message):
    """Publish a message to a queue."""
    try:
        connection = connect(rmq_config)
        channel = connection.channel()
        channel.queue_declare(queue=queue, durable=True, passive=True)
        channel.basic_publish(
            exchange='',
            routing_key=queue,
            body=message,
            properties=pika.BasicProperties(delivery_mode=2),
        )
        connection.close()
        return True
    except Exception as e:
        logger.error(f"Failed to publish message to queue '{queue}': {e}")
        return False


def publish_to_error_queue(rmq_config, error_queue, msg_type, id, identifier):
    """Publish an item to the error queue."""
    message = format_message(msg_type, id, identifier)
    if publish_to_queue(rmq_config, error_queue, message):
        logger.info(f"Sent to error queue '{error_queue}': {identifier} (attempts exceeded)")


def check_processes(processes, rmq_config=None, max_attempts=10, backoff_delay=300):
    """Remove finished subprocesses from the list and log their exit status.

    If a worker fails and rmq_config is provided:
    - Re-queue with updated timestamp and attempt counter if max_attempts not reached
    - Send to error queue if max_attempts exceeded
    """
    still_running = []
    for p, msg_dict, queue in processes:
        rc = p.poll()
        if rc is None:
            still_running.append((p, msg_dict, queue))
        else:
            identifier = msg_dict['identifier']
            if rc == 0:
                logger.info(f"Worker finished: {identifier}")
            else:
                logger.warning(f"Worker exited with code {rc}: {identifier}")
                if rmq_config:
                    attempts = int(msg_dict['attempts']) + 1
                    if attempts >= max_attempts:
                        error_queue = f"{queue}.error"
                        publish_to_error_queue(rmq_config, error_queue, msg_dict['type'], msg_dict['id'], identifier)
                    else:
                        retry_time = (datetime.now(timezone.utc) + timedelta(seconds=backoff_delay)).strftime('%Y-%m-%dT%H:%M:%SZ')
                        new_message = format_message(msg_dict['type'], msg_dict['id'], identifier, retry_time, attempts)
                        if publish_to_queue(rmq_config, queue, new_message):
                            logger.info(f"Re-queued for retry (attempt {attempts}/{max_attempts}): {identifier}")

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


def get_current_timestamp():
    """Return current UTC time as ISO-8601 string (seconds granularity)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso8601(timestamp_str):
    """Parse ISO-8601 timestamp string to datetime object."""
    return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))


def is_future_timestamp(timestamp_str):
    """Check if timestamp is in the future."""
    try:
        ts = parse_iso8601(timestamp_str)
        now = datetime.now(timezone.utc)
        return ts > now
    except (ValueError, AttributeError):
        return False


def format_message(msg_type, id, identifier, timestamp=None, attempts=0):
    """Format a message with up to 5 parts delimited by pipe."""
    parts = [msg_type, str(id), str(identifier)]
    if timestamp is not None:
        parts.append(timestamp)
        parts.append(str(attempts))
    return '|'.join(parts)


def parse_message(message):
    """Parse a message into its components.

    Returns a dict with keys: type, id, identifier, timestamp (or None), attempts (or 0)
    """
    parts = message.split('|')
    result = {
        'type': parts[0] if len(parts) > 0 else '',
        'id': parts[1] if len(parts) > 1 else '',
        'identifier': parts[2] if len(parts) > 2 else '',
        'timestamp': parts[3] if len(parts) > 3 else None,
        'attempts': parts[4] if len(parts) > 4 else 0,
    }
    # if len(parts) > 4:
    #     try:
    #         result['attempts'] = int(parts[4])
    #     except ValueError:
    #         result['attempts'] = 0
    return result


def read_queue(channel, queue):
    """
    Fetch one message without blocking.
    Returns (parsed_msg, delivery_tag) or (None, None) if the queue is empty.
    parsed_msg is a dict with keys: type, id, identifier, timestamp, attempts
    """
    try:
        method, _properties, body = channel.basic_get(queue=queue, auto_ack=False)
    except Exception as e:
        logger.error(f"Error reading from queue '{queue}': {e}")
        return None, None

    if method is None:
        return None, None

    message_str = body.decode('utf-8').strip()
    parsed = parse_message(message_str)
    return parsed, method.delivery_tag


def start_worker(identifier, id=0, ocr_only=False, recent=False):
    cmd = [sys.executable, str(SCRIPT), '--identifier', identifier , '--id', id]
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

    Handles retry logic: messages with future timestamps are re-queued.
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
                msg_dict, tag = read_queue(channel, queue)
                if msg_dict is None:
                    # The queue is empty, let's not create a loop
                    return

                # Check if message has a future timestamp - if so, re-queue and skip
                if msg_dict['timestamp'] and is_future_timestamp(msg_dict['timestamp']):
                    message_str = format_message(msg_dict['type'], msg_dict['id'], msg_dict['identifier'], msg_dict['timestamp'], msg_dict['attempts'])
                    if publish_to_queue(rmq_config, queue, message_str):
                        logger.debug(f"Re-queued delayed message (not yet ready): {msg_dict['identifier']}")
                    channel.basic_ack(delivery_tag=tag)
                    # We requeued, so let's stop here. We'll create a loop if there is 
                    # only one item in the queue and it's timestamp is in the future.
                    return

                # Process the message
                identifier = msg_dict['identifier']
                id = msg_dict['id']
                proc = start_worker(identifier, id=id, ocr_only=ocr_only, recent=recent)
                channel.basic_ack(delivery_tag=tag)
                spawned.append((proc, msg_dict, queue))

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
    backoff_delay = int(rmq.get('backoff_delay', 300))
    max_attempts = int(rmq.get('max_attempts', 10))
    error_queue_suffix = queues.get('error_queue_suffix', '').strip() or None

    logger.info(f"Starting monitor-queue (concurrency={concurrency}, backoff_delay={backoff_delay}s, max_attempts={max_attempts})")
    logger.info(f"New Queue: '{queues['new_items']}' | Updated Queue: '{queues['updated_items']}' | OCR queue: '{queues['ocr_only']}'")
    if error_queue_suffix:
        logger.info(f"Error queue suffix: '{error_queue_suffix}'")
    else:
        logger.warning("No error queue suffix configured — failed workers will only be logged")

    processes = []

    while True:
        processes = check_processes(processes, rmq_config=rmq, max_attempts=max_attempts, backoff_delay=backoff_delay)
        slots = concurrency - len(processes)

        if slots > 0:
            new_procs = read_queues(rmq, queues, slots)
            if new_procs is not None:
                processes.extend(new_procs)

        time.sleep(rmq['poll_interval'])


if __name__ == '__main__':
    main()
