#!/usr/bin/env python3
"""
Move messages from one RabbitMQ queue to another.

Usage:
    python move-queue-messages.py SOURCE_QUEUE DEST_QUEUE
    python move-queue-messages.py SOURCE_QUEUE DEST_QUEUE --count 10
    python move-queue-messages.py SOURCE_QUEUE DEST_QUEUE --all

Requires config.toml with [rabbitmq] section containing host and credentials.
"""

import sys
import argparse
import toml
import pika
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()

# Load config
config_file = PROJECT_DIR / 'config.toml'
if not config_file.exists():
    print("config.toml not found.")
    sys.exit(1)

with open(config_file, 'r') as f:
    config = toml.load(f)


def connect(rmq_config):
    """Create a RabbitMQ connection."""
    credentials = pika.PlainCredentials(
        rmq_config.get('username', 'guest'),
        rmq_config.get('password', 'guest'),
    )
    parameters = pika.ConnectionParameters(
        host=rmq_config['host'],
        port=int(rmq_config.get('port', 5672)),
        virtual_host=rmq_config.get('vhost', '/'),
        credentials=credentials,
        connection_attempts=3,
        retry_delay=5,
    )
    return pika.BlockingConnection(parameters)


def move_messages(source_queue, dest_queue, count=None):
    """Move messages from source to destination queue."""
    rmq = config['rabbitmq']

    try:
        connection = connect(rmq)
        channel = connection.channel()

        # Ensure both queues exist
        channel.queue_declare(queue=source_queue, durable=True, passive=True)
        channel.queue_declare(queue=dest_queue, durable=True, passive=True)

        moved = 0
        while True:
            # Stop if we've reached the count
            if count is not None and moved >= count:
                break

            # Fetch one message without auto-ack
            method, _properties, body = channel.basic_get(queue=source_queue, auto_ack=False)

            if method is None:
                # Queue is empty
                break

            message = body.decode('utf-8').strip()

            # Publish to destination queue
            channel.basic_publish(
                exchange='',
                routing_key=dest_queue,
                body=message,
                properties=pika.BasicProperties(delivery_mode=2),
            )

            # Acknowledge the source message
            channel.basic_ack(delivery_tag=method.delivery_tag)

            moved += 1
            print(f"Moved: {message}")

        connection.close()
        print(f"\nTotal messages moved: {moved}")
        return 0

    except pika.exceptions.AMQPConnectionError as e:
        print(f"Error: Could not connect to RabbitMQ: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description='Move messages from one RabbitMQ queue to another'
    )
    parser.add_argument('source', help='Source queue name')
    parser.add_argument('destination', help='Destination queue name')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--count', type=int, help='Number of messages to move')
    group.add_argument('--all', action='store_true', help='Move all messages (default if no --count)')

    args = parser.parse_args()

    count = None
    if args.count:
        count = args.count
    # --all means count=None (move all), which is already the default

    return move_messages(args.source, args.destination, count)


if __name__ == '__main__':
    sys.exit(main())
