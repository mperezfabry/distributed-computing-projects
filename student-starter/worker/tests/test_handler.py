"""
Tests for the AdFlow ad selection worker.

Run with: pytest worker/tests/test_handler.py -v

These tests use moto to mock AWS services. Install it with:
    pip install moto[sqs,dynamodb]

All three tests must pass before you deploy to AWS.
"""

import json
import os
import pytest

# moto must be installed: pip install moto[sqs,dynamodb]
from moto import mock_aws
import boto3

# Import the functions under test
from worker.lambda_handler import (
    compute_score,
    select_winner,
    lambda_handler,
    RELEVANCE_MAP,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

SAMPLE_OPPORTUNITY = {
    "opportunity_id": "test-001",
    "timestamp": "2025-03-10T20:15:00Z",  # hour 20 = evening peak (1.25)
    "user_id": "u_12345",
    "content_category": "sports",
    "device_type": "mobile",  # device bonus 1.1
    "region": "southeast",
    "bids": [
        {"advertiser_id": "adv_001", "bid_amount": 3.50, "category": "sportswear"},
        {"advertiser_id": "adv_002", "bid_amount": 4.20, "category": "fast_food"},
        {"advertiser_id": "adv_003", "bid_amount": 2.85, "category": "sportswear"},
    ],
}


# ---------------------------------------------------------------------------
# Test 1: Scoring function
# ---------------------------------------------------------------------------

class TestComputeScore:
    """Verify the scoring formula produces correct results."""

    def test_matched_category_evening_mobile(self):
        """
        sports + sportswear = 1.4 relevance
        hour 20 = 1.25 time bonus
        mobile = 1.1 device bonus
        score = 3.50 * 1.4 * 1.25 * 1.1 = 6.7375
        """
        bid = {"advertiser_id": "adv_001", "bid_amount": 3.50, "category": "sportswear"}
        score = compute_score(bid, SAMPLE_OPPORTUNITY)
        assert abs(score - 6.7375) < 0.01, f"Expected ~6.7375, got {score}"

    def test_unmatched_category(self):
        """
        sports + fast_food = 1.0 (no match)
        score = 4.20 * 1.0 * 1.25 * 1.1 = 5.775
        """
        bid = {"advertiser_id": "adv_002", "bid_amount": 4.20, "category": "fast_food"}
        score = compute_score(bid, SAMPLE_OPPORTUNITY)
        assert abs(score - 5.775) < 0.01, f"Expected ~5.775, got {score}"

    def test_no_time_bonus(self):
        """Hour 15 is outside all bonus windows, so time bonus = 1.0."""
        opp = {**SAMPLE_OPPORTUNITY, "timestamp": "2025-03-10T15:00:00Z"}
        bid = {"advertiser_id": "adv_001", "bid_amount": 3.50, "category": "sportswear"}
        # 3.50 * 1.4 * 1.0 * 1.1 = 5.39
        score = compute_score(bid, opp)
        assert abs(score - 5.39) < 0.01, f"Expected ~5.39, got {score}"

    def test_desktop_device(self):
        """Desktop device bonus = 1.0."""
        opp = {**SAMPLE_OPPORTUNITY, "device_type": "desktop"}
        bid = {"advertiser_id": "adv_001", "bid_amount": 3.50, "category": "sportswear"}
        # 3.50 * 1.4 * 1.25 * 1.0 = 6.125
        score = compute_score(bid, opp)
        assert abs(score - 6.125) < 0.01, f"Expected ~6.125, got {score}"


# ---------------------------------------------------------------------------
# Test 2: Winner selection
# ---------------------------------------------------------------------------

class TestSelectWinner:
    """Verify that the correct winner is selected."""

    def test_lower_bid_wins_with_relevance(self):
        """
        adv_001 (sportswear, $3.50) should beat adv_002 (fast_food, $4.20)
        because the relevance multiplier overcomes the raw bid difference.
        """
        result = select_winner(SAMPLE_OPPORTUNITY)
        assert result is not None
        assert result["winning_advertiser_id"] == "adv_001"

    def test_score_margin_positive(self):
        """The margin should be the gap between first and second place."""
        result = select_winner(SAMPLE_OPPORTUNITY)
        assert result["score_margin"] > 0

    def test_no_bids(self):
        """An opportunity with no bids should return None."""
        opp = {**SAMPLE_OPPORTUNITY, "bids": []}
        result = select_winner(opp)
        assert result is None


# ---------------------------------------------------------------------------
# Test 3: Full batch processing (with mocked AWS)
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    """End-to-end test with mocked SQS and DynamoDB."""

    @mock_aws
    def test_batch_processing(self):
        """Process a batch of SQS messages and verify results appear."""
        region = "us-east-1"

        # Create mock resources
        sqs_client = boto3.client("sqs", region_name=region)
        dynamodb_client = boto3.client("dynamodb", region_name=region)

        results_queue = sqs_client.create_queue(QueueName="adflow-test-results")
        results_url = results_queue["QueueUrl"]

        dynamodb_client.create_table(
            TableName="adflow-test-results",
            AttributeDefinitions=[
                {"AttributeName": "opportunity_id", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "opportunity_id", "KeyType": "HASH"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Set environment variables
        os.environ["RESULTS_QUEUE_URL"] = results_url
        os.environ["DYNAMO_TABLE_NAME"] = "adflow-test-results"
        os.environ["AWS_DEFAULT_REGION"] = region

        # Rebuild clients to use moto
        import worker.lambda_handler as handler
        handler.sqs = boto3.client("sqs", region_name=region)
        handler.dynamodb = boto3.resource("dynamodb", region_name=region)
        handler.RESULTS_QUEUE_URL = results_url
        handler.DYNAMO_TABLE_NAME = "adflow-test-results"

        # Build a fake SQS event with two opportunities
        event = {
            "Records": [
                {
                    "messageId": "msg-001",
                    "body": json.dumps(SAMPLE_OPPORTUNITY),
                },
                {
                    "messageId": "msg-002",
                    "body": json.dumps({
                        **SAMPLE_OPPORTUNITY,
                        "opportunity_id": "test-002",
                    }),
                },
            ]
        }

        # Invoke the handler
        result = lambda_handler(event, None)

        # Verify no failures
        assert len(result["batchItemFailures"]) == 0

        # Verify results landed in SQS
        msgs = sqs_client.receive_message(
            QueueUrl=results_url,
            MaxNumberOfMessages=10,
        )
        assert len(msgs.get("Messages", [])) == 2

        # Verify results landed in DynamoDB
        ddb = boto3.resource("dynamodb", region_name=region)
        table = ddb.Table("adflow-test-results")
        scan = table.scan()
        assert scan["Count"] == 2
