# AdFlow - Real-Time Ad Selection Pipeline

## Quick Start

### 1. Set your student ID

Pick a short ID (your name, initials, etc.) and use it everywhere. Example: `gsalu`

All your AWS resources will be named `adflow-{yourid}-*`.

### 2. Deploy with SAM

```bash
sam build
sam deploy --guided --stack-name adflow-YOURID
```

When prompted for `StudentId`, enter your chosen ID.

After deployment, note the outputs -- you will need the queue URLs for the test apparatus.

### 3. Verify deployment

```bash
# Check your Lambda exists
aws lambda get-function --function-name adflow-YOURID-worker

# Check your queues
aws sqs get-queue-url --queue-name adflow-YOURID-input
aws sqs get-queue-url --queue-name adflow-YOURID-results

# Check your DynamoDB table
aws dynamodb describe-table --table-name adflow-YOURID-results

# Tail your Lambda logs (live)
aws logs tail /aws/lambda/adflow-YOURID-worker --follow
```

### 4. Run tests locally before deploying

```bash
pip install moto[sqs,dynamodb] pytest
pytest worker/tests/test_handler.py -v
```

All three test classes must pass before you deploy.

### 5. Test with the test apparatus

Open the test apparatus web page, enter your student ID, select a test profile,
and run. Watch your Lambda process messages and results appear in DynamoDB.

### 6. Cleanup between test runs

```bash
python cleanup.py --student-id YOURID --region us-east-1
```

### 7. Tear down when done

```bash
sam delete --stack-name adflow-YOURID
```

## Project Structure

```
adflow-YOURID/
  template.yaml              # SAM template (creates all AWS resources)
  worker/
    lambda_handler.py         # YOUR CODE - implement the four tasks
    requirements.txt          # Dependencies (boto3)
    tests/
      test_handler.py         # Three test suites - all must pass
  analysis/
    analysis.ipynb            # Analyst report (Q1-Q4)
  cleanup.py                  # Queue purge + table recreate
  README.md                   # This file
```

## Viewing Logs

Your Lambda writes structured logs to CloudWatch. To view them:

```bash
# Live tail (follows new log entries)
aws logs tail /aws/lambda/adflow-YOURID-worker --follow

# Search for specific patterns
aws logs filter-log-events \
    --log-group-name /aws/lambda/adflow-YOURID-worker \
    --filter-pattern "Batch complete"

# Filter by time range
aws logs filter-log-events \
    --log-group-name /aws/lambda/adflow-YOURID-worker \
    --start-time 1710000000000 \
    --filter-pattern "ERROR"
```

In the AWS Console: **CloudWatch > Log groups > /aws/lambda/adflow-YOURID-worker**

## Naming Convention

| Resource | Name |
|----------|------|
| Input Queue | `adflow-YOURID-input` |
| Results Queue | `adflow-YOURID-results` |
| DynamoDB Table | `adflow-YOURID-results` |
| Lambda Function | `adflow-YOURID-worker` |
| CloudWatch Logs | `/aws/lambda/adflow-YOURID-worker` |
