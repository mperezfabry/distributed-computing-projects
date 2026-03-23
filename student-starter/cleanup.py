"""
AdFlow Cleanup Utility
======================

Purges both SQS queues and recreates the DynamoDB table for a given student ID.
Run this between test cycles to start fresh.

Usage:
    python cleanup.py --student-id gsalu --region us-east-1

What it does:
    1. Purges adflow-{id}-input queue
    2. Purges adflow-{id}-results queue
    3. Deletes and recreates adflow-{id}-results DynamoDB table
"""

import argparse
import time
import boto3
from botocore.exceptions import ClientError


def get_queue_url(sqs, queue_name):
    """Look up a queue URL by name. Returns None if not found."""
    try:
        resp = sqs.get_queue_url(QueueName=queue_name)
        return resp["QueueUrl"]
    except ClientError as e:
        if "NonExistentQueue" in str(e) or "QueueDoesNotExist" in str(e):
            return None
        raise


def purge_queue(sqs, queue_name):
    """Purge all messages from a queue."""
    url = get_queue_url(sqs, queue_name)
    if url is None:
        print(f"  Queue '{queue_name}' not found -- skipping.")
        return

    try:
        sqs.purge_queue(QueueUrl=url)
        print(f"  Purged: {queue_name}")
    except ClientError as e:
        if "PurgeQueueInProgress" in str(e):
            print(f"  Purge already in progress for {queue_name} (SQS allows one per 60s)")
        else:
            raise


def recreate_table(dynamodb, table_name):
    """Delete and recreate a DynamoDB table with the standard schema."""
    # Try to delete
    try:
        dynamodb.delete_table(TableName=table_name)
        print(f"  Deleting table: {table_name}...")
        waiter = dynamodb.get_waiter("table_not_exists")
        waiter.wait(TableName=table_name, WaiterConfig={"Delay": 2, "MaxAttempts": 30})
        print(f"  Deleted.")
    except ClientError as e:
        if "ResourceNotFoundException" in str(e):
            print(f"  Table '{table_name}' does not exist -- creating fresh.")
        else:
            raise

    # Recreate
    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "opportunity_id", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "opportunity_id", "KeyType": "HASH"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    print(f"  Creating table: {table_name}...")
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name, WaiterConfig={"Delay": 2, "MaxAttempts": 30})
    print(f"  Table ready.")


def main():
    parser = argparse.ArgumentParser(description="AdFlow cleanup utility")
    parser.add_argument("--student-id", required=True, help="Your student ID (e.g., gsalu)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--skip-table", action="store_true",
        help="Skip DynamoDB table recreation (only purge queues)",
    )
    args = parser.parse_args()

    sid = args.student_id.lower().strip()
    input_q = f"adflow-{sid}-input"
    results_q = f"adflow-{sid}-results"
    table_name = f"adflow-{sid}-results"

    print(f"AdFlow Cleanup for student: {sid}")
    print(f"  Region: {args.region}")
    print()

    sqs = boto3.client("sqs", region_name=args.region)
    dynamodb_client = boto3.client("dynamodb", region_name=args.region)

    print("Step 1: Purging input queue")
    purge_queue(sqs, input_q)

    print("Step 2: Purging results queue")
    purge_queue(sqs, results_q)

    if not args.skip_table:
        print("Step 3: Recreating DynamoDB table")
        recreate_table(dynamodb_client, table_name)

    print()
    print("Cleanup complete. Ready for a fresh test run.")


if __name__ == "__main__":
    main()
