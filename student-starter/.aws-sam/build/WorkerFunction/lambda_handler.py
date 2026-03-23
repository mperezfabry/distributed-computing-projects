"""
AdFlow Ad Selection Worker
===========================

This Lambda function processes ad opportunity messages from an SQS queue,
applies a quality-adjusted scoring function to select winning bids,
and posts results onward.

Your Tasks:
    1. implement compute_score()    - the scoring formula
    2. implement select_winner()    - pick the winning bid
    3. implement process_opportunity() - full message processing
    4. implement lambda_handler()   - batch processing with failure handling

Logging and Performance:
    Use the logger for all output (not print). Example:
        logger.info("Processed %s in %.1f ms", opportunity_id, elapsed_ms)

    Measure wall-clock time for performance:
        start = time.perf_counter()
        # ... do work ...
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("Operation took %.1f ms", elapsed_ms)

    To view your logs after deployment:
        aws logs tail /aws/lambda/adflow-YOURID-worker --follow

    To search logs for specific patterns:
        aws logs filter-log-events \
            --log-group-name /aws/lambda/adflow-YOURID-worker \
            --filter-pattern "Batch complete"

    In the AWS Console:
        CloudWatch > Log groups > /aws/lambda/adflow-YOURID-worker
"""

import json
import os
import time
import logging
from datetime import datetime, timezone
from decimal import Decimal

import boto3


# ---------------------------------------------------------------------------
# Logging - CloudWatch picks up anything written to the logger
# ---------------------------------------------------------------------------
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# AWS clients - created once per cold start, reused across invocations
# ---------------------------------------------------------------------------
sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")

RESULTS_QUEUE_URL = os.environ.get("RESULTS_QUEUE_URL", "")
DYNAMO_TABLE_NAME = os.environ.get("DYNAMO_TABLE_NAME", "")

# ---------------------------------------------------------------------------
# Scoring constants - from the assignment specification
# ---------------------------------------------------------------------------

# Relevance multiplier: (content_category, advertiser_category) -> multiplier
# Any combination not listed here receives 1.0
RELEVANCE_MAP = {
    ("sports", "sportswear"): 1.4,
    ("sports", "energy_drink"): 1.3,
    ("finance", "fintech"): 1.5,
    ("finance", "insurance"): 1.3,
    ("entertainment", "streaming"): 1.4,
    ("entertainment", "gaming"): 1.3,
    ("lifestyle", "beauty"): 1.3,
    ("lifestyle", "travel"): 1.2,
}

# Time bonus: (start_hour_inclusive, end_hour_exclusive, bonus)
TIME_WINDOWS = [
    (6, 9, 1.20),     # Morning commute
    (12, 14, 1.15),   # Lunch browsing
    (19, 23, 1.25),   # Evening peak
]

# Device bonus
DEVICE_BONUS = {
    "mobile": 1.1,
    "desktop": 1.0,
}


# ---------------------------------------------------------------------------
# Task 1: Scoring Function
# ---------------------------------------------------------------------------

def compute_score(bid, opportunity):
    """
    Compute the quality-adjusted score for a single bid.

    Formula:
        score = bid_amount * relevance_multiplier * time_bonus * device_bonus

    Args:
        bid (dict): Keys: advertiser_id, bid_amount, category
        opportunity (dict): Keys: content_category, device_type, timestamp

    Returns:
        float: the computed score

    TODO: Implement this function. Handle edge cases:
        - What if bid_amount is missing or zero?
        - What if the category combination is not in RELEVANCE_MAP?
        - What if the timestamp cannot be parsed?
    """
    bid_amount = bid.get("bid_amount", 0)
    if not bid_amount or bid_amount <= 0:
        return 0.0
    
    content_cat = opportunity.get("content_category")
    device = opportunity.get("device_type")
    timestamp = opportunity.get("timestamp")
    advertiser_cat = bid.get("category")

    time_bonus = 1.0
    if timestamp:
        try:
            dt_obj = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            hour = dt_obj.hour
            for start, end, bonus in TIME_WINDOWS:
                if start <= hour < end:
                    time_bonus = bonus
                    break
        except ValueError:
            logger.warning("Invalid timestamp format: %s", timestamp)
            time_bonus = 1.0

    relevance_multiplier = RELEVANCE_MAP.get((content_cat, advertiser_cat), 1.0)
    device_bonus = DEVICE_BONUS.get(device, 1.0)

    score = bid_amount * relevance_multiplier * time_bonus * device_bonus
    return score


# ---------------------------------------------------------------------------
# Task 2: Winner Selection
# ---------------------------------------------------------------------------

def select_winner(opportunity):
    """
    Evaluate all bids for an opportunity and return the winner.

    Args:
        opportunity (dict): Full opportunity including 'bids' list.

    Returns:
        dict with keys:
            winning_advertiser_id (str)
            winning_bid_amount (float)
            winning_score (float)
            score_margin (float) - winning score minus second-place score
        Returns None if there are no valid bids.

    TODO: Implement this function.
        - Score every bid using compute_score()
        - Find the highest score (the winner) and second-highest score
        - Return the result dict with all four fields
    """
    bids = opportunity.get("bids", [])
    if not bids:
        return None

    scored_bids = []
    for bid in bids:
        score = compute_score(bid, opportunity)
        if score > 0:
            scored_bids.append({
                "advertiser_id": bid.get("advertiser_id"),
                "bid_amount": bid.get("bid_amount"),
                "score": score
            })

    if not scored_bids:
        return None

    # Sort by score descending
    scored_bids.sort(key=lambda x: x["score"], reverse=True)

    winner = scored_bids[0]
    second_score = scored_bids[1]["score"] if len(scored_bids) > 1 else 0.0

    return {
        "winning_advertiser_id": winner["advertiser_id"],
        "winning_bid_amount": float(winner["bid_amount"]),
        "winning_score": float(winner["score"]),
        "score_margin": float(winner["score"] - second_score)
    }


# ---------------------------------------------------------------------------
# Task 3: Process a Single Opportunity
# ---------------------------------------------------------------------------

def process_opportunity(opportunity):
    """
    Process one opportunity end-to-end:
        1. Select the winning bid
        2. Construct the result record (see result schema below)
        3. Send the result where it needs to go

    Result record schema:
        opportunity_id (str)       - copied from input
        content_category (str)     - copied from input (needed for Part 5 analysis)
        winning_advertiser_id (str)
        winning_bid_amount (float)
        winning_score (float)
        score_margin (float)
        processed_at (str)         - ISO 8601 timestamp of when you processed it

    The processed_at timestamp is how latency is measured. The difference
    between the opportunity's timestamp and your processed_at is the
    end-to-end processing time for that auction.

    Think about:
        - What gets measured (latency on what path)?
        - What is the right order of operations for efficiency?
        - How do you generate the processed_at timestamp?

    Args:
        opportunity (dict): A single ad opportunity message.

    Returns:
        dict: The result record, or None if no valid bids.

    TODO: Implement this function.
    """
    start = time.perf_counter()
    opportunity_id = opportunity.get("opportunity_id")
    content_category = opportunity.get("content_category")

    winner_result = select_winner(opportunity)
    if not winner_result:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("Processed %s: No winner in %.1f ms", opportunity_id, elapsed_ms)
        return None

    processed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    result = {
        "opportunity_id": opportunity_id,
        "content_category": content_category,
        "winning_advertiser_id": winner_result["winning_advertiser_id"],
        "winning_bid_amount": winner_result["winning_bid_amount"],
        "winning_score": winner_result["winning_score"],
        "score_margin": winner_result["score_margin"],
        "processed_at": processed_at
    }

    # Save to DynamoDB
    if DYNAMO_TABLE_NAME:
        try:
            table = dynamodb.Table(DYNAMO_TABLE_NAME)
            # DynamoDB requires Decimal for floats
            ddb_item = json.loads(json.dumps(result), parse_float=Decimal)
            table.put_item(Item=ddb_item)
        except Exception as e:
            logger.error("DynamoDB error for %s: %s", opportunity_id, e)
            raise

    # Send to Results Queue
    if RESULTS_QUEUE_URL:
        try:
            sqs.send_message(
                QueueUrl=RESULTS_QUEUE_URL,
                MessageBody=json.dumps(result)
            )
        except Exception as e:
            logger.error("SQS error for %s: %s", opportunity_id, e)
            raise

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info("Processed %s in %.1f ms", opportunity_id, elapsed_ms)
    return result


# ---------------------------------------------------------------------------
# Task 4: Lambda Entry Point with Batch Processing
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    Lambda entry point. Receives a batch of SQS messages.

    The event contains a 'Records' list. Each record has a 'body' field
    with the JSON-encoded opportunity message, and a 'messageId' field.

    Requirements:
        - Process every message in the batch
        - If a message fails, do NOT let it crash the entire batch
        - Return partial batch failures so SQS retries only the failed messages
        - Log timing for the entire batch

    Return format for partial batch failures:
        {
            "batchItemFailures": [
                {"itemIdentifier": "message-id-that-failed"},
                ...
            ]
        }

    If you return an empty batchItemFailures list, SQS considers all
    messages successfully processed and deletes them from the queue.

    Args:
        event (dict): SQS event with 'Records' list
        context: Lambda context (has aws_request_id, function_name, etc.)

    Returns:
        dict with batchItemFailures

    TODO: Implement this function.
    """
    start_batch = time.perf_counter()
    batch_item_failures = []
    records = event.get("Records", [])

    for record in records:
        message_id = record.get("messageId")
        try:
            body = json.loads(record.get("body", "{}"))
            process_opportunity(body)
        except Exception as e:
            logger.error("Failed message %s: %s", message_id, e, exc_info=True)
            batch_item_failures.append({"itemIdentifier": message_id})

    elapsed_ms = (time.perf_counter() - start_batch) * 1000
    logger.info("Batch complete: %d records, %d failures in %.1f ms",
                len(records), len(batch_item_failures), elapsed_ms)

    return {"batchItemFailures": batch_item_failures}
