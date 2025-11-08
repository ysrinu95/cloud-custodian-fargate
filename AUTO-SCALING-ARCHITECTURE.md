# ECS Service Auto-Scaling Architecture (Scale to Zero)

## Overview
Implemented **ECS Service with auto-scaling that scales to zero** when idle. Containers are created dynamically only when security events occur, based on SQS queue depth.

---

## Architecture Flow

```
EventBridge → Lambda (parse + select policy + queue to SQS) 
                ↓
            SQS Queue (messages accumulate)
                ↓
    Auto-Scaling detects messages → Scales ECS Service from 0 to N tasks
                ↓
    ECS Fargate Tasks (poll SQS + execute policies)
                ↓
    When queue empty → Tasks exit gracefully → Service scales back to 0
```

---

## Key Features

### ✅ Cost-Efficient (Scale to Zero)
- **Idle state:** 0 running containers = $0/hour
- **Active state:** Tasks scale up automatically based on workload
- **Auto-scale down:** Tasks exit after 3 empty SQS receives

### ✅ Event-Driven
- Containers created **only when security findings arrive**
- No continuous polling when idle
- Fast response: tasks spawn within 60 seconds of queue activity

### ✅ Elastic Scaling
- **Min capacity:** 0 tasks
- **Max capacity:** 10 tasks  
- **Target:** 5 messages per task
- **Scale-out:** 1 minute cooldown
- **Scale-in:** 5 minutes cooldown

---

## Components Updated

### 1. Terraform Infrastructure

#### Added: ECS Service with Auto-Scaling
```hcl
resource "aws_ecs_service" "worker" {
  desired_count = 0  # Start with zero tasks
  # ... configuration
}

resource "aws_appautoscaling_target" "ecs_target" {
  min_capacity = 0   # Can scale down to zero
  max_capacity = 10  # Max 10 concurrent tasks
}

resource "aws_appautoscaling_policy" "ecs_policy_scale_up" {
  # Target: 5 messages per task
  # Metric: SQS ApproximateNumberOfMessagesVisible
}
```

**Scaling Behavior:**
- 0 messages → 0 tasks
- 1-5 messages → 1 task
- 6-10 messages → 2 tasks  
- 11-15 messages → 3 tasks
- ... up to 50+ messages → 10 tasks

---

### 2. Lambda Function (Invoker)

**Purpose:** Parse findings, select policy, send to SQS

**Key Changes:**
- Removed ECS `run_task()` code
- Uses `sqs.send_message()` for queueing
- Logs: "ECS Service will auto-scale to process this message"

**Environment Variables:**
- `SQS_QUEUE_URL` - Queue for findings
- `POLICY_BUCKET` - S3 bucket with policies
- `POLICY_MAPPING_KEY` - config/policy-mappings.json
- `DEFAULT_POLICY_KEY` - policies/s3-createbucket.yml

---

### 3. Worker Application (ECS Fargate Tasks)

**Purpose:** Poll SQS, execute policies, exit when idle

**Key Features:**

#### Graceful Shutdown (Scale to Zero)
```python
MAX_EMPTY_RECEIVES = 3  # Exit after 3 empty polls

consecutive_empty_receives = 0
while True:
    messages = sqs.receive_message(...)
    
    if not messages:
        consecutive_empty_receives += 1
        if consecutive_empty_receives >= MAX_EMPTY_RECEIVES:
            print("Exiting to allow scale-down to 0")
            sys.exit(0)  # Graceful exit
```

**Behavior:**
- Polls SQS with 20-second long polling
- Processes up to 10 messages per batch
- After 3 consecutive empty receives (~60 seconds), exits
- ECS Service detects task exit → scales down desired count

**Environment Variables:**
- `SQS_QUEUE_URL` (required)
- `POLICY_BUCKET` (required)
- `OUTPUT_BUCKET` (optional)
- `MAX_EMPTY_RECEIVES` (default: 3)
- `WAIT_TIME_SECONDS` (default: 20)

---

## Scaling Scenarios

### Scenario 1: Idle State (No Events)
```
Time    | Queue | Desired | Running | Cost
--------|-------|---------|---------|------
0:00    |   0   |    0    |    0    | $0
0:30    |   0   |    0    |    0    | $0
1:00    |   0   |    0    |    0    | $0
```
**Cost:** $0 per hour

---

### Scenario 2: Single Event
```
Time    | Queue | Desired | Running | Action
--------|-------|---------|---------|------------------
0:00    |   1   |    0    |    0    | Event arrives
0:01    |   1   |    1    |    0    | Auto-scaling triggered
0:02    |   1   |    1    |    1    | Task starting
0:03    |   0   |    1    |    1    | Task processes message
0:04    |   0   |    1    |    1    | Empty poll #1
0:05    |   0   |    1    |    1    | Empty poll #2
0:06    |   0   |    1    |    1    | Empty poll #3 → Exit
0:07    |   0   |    0    |    0    | Task stopped
```
**Cost:** ~$0.0002 (4 minutes × 0.25 vCPU)

---

### Scenario 3: Burst of Events
```
Time    | Queue | Desired | Running | Action
--------|-------|---------|---------|------------------
0:00    |  25   |    0    |    0    | 25 events arrive
0:01    |  25   |    5    |    0    | Scale to 5 tasks
0:02    |  25   |    5    |    3    | Tasks starting
0:03    |  15   |    5    |    5    | Processing messages
0:05    |   5   |    5    |    5    | Processing continues
0:07    |   0   |    3    |    5    | Scale-in starts
0:08    |   0   |    0    |    2    | Tasks exiting
0:10    |   0   |    0    |    0    | All tasks stopped
```
**Cost:** ~$0.003 (10 minutes × 5 tasks avg × 0.25 vCPU)

---

## Cost Analysis

### Previous Architecture (On-Demand Tasks)
- Lambda triggers individual ECS tasks
- Cost: ~$0.0001 per finding
- No idle cost, but task startup overhead

### Current Architecture (Auto-Scaling Service)
- Tasks poll SQS queue  
- Cost when idle: **$0**
- Cost per finding: ~$0.0001-0.0002 (includes polling overhead)
- More efficient for burst workloads

### Comparison Table

| Metric | On-Demand Tasks | Auto-Scaling Service |
|--------|----------------|---------------------|
| Idle cost | $0 | $0 |
| Cost per finding | $0.0001 | $0.0001-0.0002 |
| Startup latency | 60-90s | 30-60s (after first scale) |
| Batch efficiency | Low | High |
| Complexity | Medium | Low |
| Best for | Sporadic events | Burst workloads |

**Recommendation:** Auto-scaling service is better for:
- Predictable event patterns
- Burst workloads (multiple findings at once)
- Lower operational complexity

---

## Configuration Reference

### Auto-Scaling Policy Parameters

```hcl
target_value       = 5.0   # Target messages per task
scale_in_cooldown  = 300   # 5 min before scaling down
scale_out_cooldown = 60    # 1 min before scaling up again

min_capacity = 0   # Can scale to zero
max_capacity = 10  # Maximum concurrent tasks
```

**Tuning Guidelines:**

| Workload Type | target_value | max_capacity | scale_in_cooldown |
|---------------|--------------|--------------|-------------------|
| Low volume | 5 | 3 | 180s |
| Medium volume | 5 | 10 | 300s |
| High volume | 10 | 20 | 300s |
| Latency-sensitive | 2 | 15 | 600s |

---

### Worker Shutdown Behavior

```python
MAX_EMPTY_RECEIVES = 3      # Exit after N empty polls
WAIT_TIME_SECONDS = 20      # SQS long polling duration
```

**Shutdown Time Calculation:**
```
shutdown_time = MAX_EMPTY_RECEIVES × WAIT_TIME_SECONDS
              = 3 × 20 = 60 seconds
```

**Tuning:**
- **Fast scale-down:** `MAX_EMPTY_RECEIVES = 2` (40s shutdown)
- **Stable scale-down:** `MAX_EMPTY_RECEIVES = 5` (100s shutdown)
- **Current setting:** `3` (60s shutdown) - balanced approach

---

## Monitoring

### CloudWatch Metrics to Watch

#### Auto-Scaling Metrics
```
Namespace: AWS/ECS
- CPUUtilization (Cluster/Service)
- MemoryUtilization (Cluster/Service)
- DesiredTaskCount (Service)
- RunningTaskCount (Service)
```

#### SQS Metrics
```
Namespace: AWS/SQS
- ApproximateNumberOfMessagesVisible (trigger for scaling)
- ApproximateAgeOfOldestMessage (latency indicator)
- NumberOfMessagesReceived
- NumberOfMessagesDeleted
```

#### Custom Metrics
```
Namespace: CloudCustodian/FargateWorker
- MessagesReceived
- ProcessingSuccesses
- ProcessingFailures

Namespace: CloudCustodian/PolicyExecution
- ExecutionTime
- ResourcesProcessed
- ActionsTaken
```

---

### CloudWatch Alarms

#### Scaling Health
```bash
# Alert if service can't scale down
aws cloudwatch put-metric-alarm \
  --alarm-name ecs-service-stuck-running \
  --metric-name DesiredTaskCount \
  --namespace AWS/ECS \
  --statistic Average \
  --period 600 \
  --threshold 1 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 6 \
  --dimensions Name=ServiceName,Value=custodian-worker
```

#### Queue Backlog
```bash
# Alert if messages not being processed
aws cloudwatch put-metric-alarm \
  --alarm-name sqs-queue-backlog \
  --metric-name ApproximateAgeOfOldestMessage \
  --namespace AWS/SQS \
  --statistic Maximum \
  --period 300 \
  --threshold 600 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2
```

---

## Testing

### Test 1: Verify Scale from Zero
```bash
# 1. Check initial state (should be 0)
aws ecs describe-services \
  --cluster custodian-cluster \
  --services custodian-worker \
  --query 'services[0].{desired:desiredCount,running:runningCount}'

# 2. Send test event to Lambda
aws lambda invoke \
  --function-name custodian-invoker \
  --payload file://test-event.json \
  response.json

# 3. Wait 60 seconds, check again (should be 1)
aws ecs describe-services \
  --cluster custodian-cluster \
  --services custodian-worker \
  --query 'services[0].{desired:desiredCount,running:runningCount}'

# 4. Wait 2 minutes (should scale back to 0)
aws ecs describe-services \
  --cluster custodian-cluster \
  --services custodian-worker \
  --query 'services[0].{desired:desiredCount,running:runningCount}'
```

### Test 2: Load Test (Burst)
```bash
# Send 20 events rapidly
for i in {1..20}; do
  aws lambda invoke \
    --function-name custodian-invoker \
    --payload file://test-event.json \
    --no-cli-pager \
    response-$i.json &
done
wait

# Monitor scaling
watch -n 5 'aws ecs describe-services --cluster custodian-cluster --services custodian-worker --query "services[0].{desired:desiredCount,running:runningCount}"'
```

### Test 3: Graceful Shutdown
```bash
# Check worker logs for shutdown message
aws logs tail /aws/ecs/custodian-worker --follow | grep "Exiting gracefully"
```

**Expected output:**
```
No messages for extended period. Exiting gracefully to allow scale-down to 0.
```

---

## Troubleshooting

### Issue: Service Not Scaling to Zero

**Symptoms:** Tasks remain running even when queue is empty

**Possible Causes:**
1. Worker not exiting gracefully
2. Long message visibility timeout
3. Scale-in cooldown too long

**Debug Steps:**
```bash
# 1. Check worker logs
aws logs tail /aws/ecs/custodian-worker --since 10m

# 2. Check SQS queue
aws sqs get-queue-attributes \
  --queue-url <queue-url> \
  --attribute-names ApproximateNumberOfMessages

# 3. Check auto-scaling activities
aws application-autoscaling describe-scaling-activities \
  --service-namespace ecs \
  --resource-id service/custodian-cluster/custodian-worker
```

**Solutions:**
- Reduce `MAX_EMPTY_RECEIVES` in worker
- Decrease `scale_in_cooldown` in Terraform
- Check for stuck messages in SQS

---

### Issue: Slow Scale-Up

**Symptoms:** Tasks take >2 minutes to start processing messages

**Possible Causes:**
1. ECR image pull time
2. Task placement delays
3. Scale-out cooldown

**Solutions:**
```hcl
# Reduce scale-out cooldown
scale_out_cooldown = 30  # Was 60

# Use smaller base image
FROM python:3.11-slim  # Already optimal

# Pre-warm service (keep 1 task minimum)
min_capacity = 1  # Was 0
```

---

### Issue: Tasks Exiting Too Quickly

**Symptoms:** Tasks exit before processing all messages

**Debug:**
```bash
# Check task stop reasons
aws ecs describe-tasks \
  --cluster custodian-cluster \
  --tasks <task-arn> \
  --query 'tasks[0].stoppedReason'
```

**Solution:**
```python
# Increase MAX_EMPTY_RECEIVES
MAX_EMPTY_RECEIVES = 5  # Was 3
```

---

## Deployment

### Step 1: Deploy Terraform
```bash
cd terraform
terraform init
terraform plan
terraform apply
```

**Expected changes:**
- ✅ Add `aws_ecs_service.worker` (desired_count=0)
- ✅ Add `aws_appautoscaling_target.ecs_target`
- ✅ Add `aws_appautoscaling_policy.ecs_policy_scale_up`

### Step 2: Update Lambda
```bash
cd lambda
zip -r lambda-function.zip invoker_lambda.py
aws lambda update-function-code \
  --function-name custodian-invoker \
  --zip-file fileb://lambda-function.zip
```

### Step 3: Build and Push Worker Image
```bash
cd ecs-worker
docker build -t custodian-worker .
aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-repo>
docker tag custodian-worker <ecr-repo>:latest
docker push <ecr-repo>:latest
```

### Step 4: Verify Deployment
```bash
# Service should exist with 0 desired tasks
aws ecs describe-services \
  --cluster custodian-cluster \
  --services custodian-worker

# Auto-scaling should be configured
aws application-autoscaling describe-scalable-targets \
  --service-namespace ecs \
  --resource-ids service/custodian-cluster/custodian-worker
```

---

## Advantages Over Previous Approach

### ✅ Simpler Architecture
- No Lambda-triggered individual tasks
- Standard ECS Service patterns
- Familiar auto-scaling behavior

### ✅ Better for Batching
- Tasks process multiple messages per invocation
- More efficient than 1 task per message

### ✅ Cost-Efficient When Idle
- Scales to **zero** (no costs)
- Only pay for active processing time

### ✅ Built-in Resilience
- ECS Service handles task failures
- Auto-restarts unhealthy tasks
- Dead-letter queue for failed messages

### ✅ Operational Simplicity
- Standard CloudWatch metrics
- AWS Auto Scaling console visibility
- No custom orchestration logic

---

## Summary

| Feature | Status |
|---------|--------|
| Scale to zero when idle | ✅ |
| Auto-scale based on queue depth | ✅ |
| Graceful shutdown | ✅ |
| Cost-efficient | ✅ |
| Handles burst workloads | ✅ |
| Standard AWS patterns | ✅ |

**Architecture Type:** Event-driven auto-scaling  
**Scaling Range:** 0 to 10 tasks  
**Scaling Trigger:** SQS ApproximateNumberOfMessagesVisible  
**Idle Cost:** $0  
**Best For:** Variable workloads with occasional bursts

---

**Status:** ✅ Production Ready  
**Deployment Time:** ~20 minutes  
**Risk Level:** Low (standard AWS patterns)
