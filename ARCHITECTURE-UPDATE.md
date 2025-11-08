# Architecture Update: Event-Driven On-Demand ECS Tasks

## Overview
Converted from continuous SQS-polling architecture to **event-driven on-demand ECS Fargate task execution** for cost optimization.

## Previous Architecture (SQS-based)
```
EventBridge → Lambda (parse + queue) → SQS → ECS Service (continuous polling) → Policy Execution
```
**Issues:**
- ECS Service runs continuously (24/7 costs)
- Polling overhead even without events
- Underutilized resources during low activity

## New Architecture (On-Demand)
```
EventBridge → Lambda (parse + select policy + trigger task) → ECS Fargate Task (execute + exit)
```
**Benefits:**
- ✅ Zero cost when idle (no continuous running)
- ✅ Tasks spawn only on security findings
- ✅ Auto-scaling by design (one task per finding)
- ✅ No polling overhead
- ✅ Simpler infrastructure (no SQS dependency)

---

## Changes Made

### 1. Terraform Infrastructure (`terraform/unified-event-driven.tf`)

#### Removed:
- **ECS Service resource** (`aws_ecs_service.worker`) - Lines ~570-590
  - No longer running containers 24/7
  - Replaced with on-demand task execution

#### Added:
- **ECS RunTask permissions** to Lambda IAM role:
  ```hcl
  "ecs:RunTask",
  "ecs:DescribeTasks",
  "iam:PassRole"  # For task execution and task roles
  ```

- **ECS configuration environment variables** for Lambda:
  ```hcl
  ECS_CLUSTER           = aws_ecs_cluster.custodian.name
  ECS_TASK_DEFINITION   = aws_ecs_task_definition.custodian_worker.family
  ECS_SUBNETS           = join(",", aws_subnet.private[*].id)
  ECS_SECURITY_GROUP    = aws_security_group.fargate_worker.id
  ```

#### Retained:
- ECS Cluster
- ECS Task Definition (updated for single-execution mode)
- Task Execution Role
- Task Role (for Custodian permissions)

---

### 2. Lambda Function (`lambda/invoker_lambda.py`)

#### Updated:
- **Removed SQS client dependency** (now optional/deprecated)
- **Added ECS client** for task invocation
- **New `trigger_ecs_task()` function**:
  ```python
  def trigger_ecs_task(finding, policy_key, request_id):
      # Prepares finding payload as JSON
      # Calls ecs.run_task() with Fargate launch type
      # Passes data via container environment overrides
      # Returns task ARN
  ```

#### Environment Variables Passed to ECS Task:
- `FINDING_PAYLOAD` - Complete finding JSON from Lambda
- `POLICY_BUCKET` - S3 bucket containing policies
- `POLICY_KEY` - Selected policy file path

#### Handler Flow:
1. Parse EventBridge security finding
2. Dynamically select policy via `policy-mappings.json`
3. Enrich finding (optional)
4. **Trigger ECS Fargate task** (new!)
5. Return task ARN to caller

---

### 3. Worker Application (`ecs-worker/worker.py`)

#### Architecture Change:
**Before:** Continuous SQS polling loop
```python
while True:
    messages = sqs.receive_message(...)
    for msg in messages:
        process_message(msg)
```

**After:** Single-execution mode
```python
def main():
    # Read FINDING_PAYLOAD from environment
    payload = json.loads(os.environ['FINDING_PAYLOAD'])
    finding = payload['finding']
    
    # Process once
    result = process_finding(finding)
    
    # Exit
    sys.exit(0 if result['success'] else 1)
```

#### Key Changes:
- **Removed:** SQS polling loop, message visibility timeout, empty receive counter
- **Added:** Environment variable parsing for `FINDING_PAYLOAD`
- **Updated:** `main()` now processes one finding and exits
- **Simplified:** No message deletion or retry logic (handled by ECS/Lambda)

#### Environment Variables:
- `FINDING_PAYLOAD` (required) - JSON string from Lambda
- `POLICY_BUCKET` (required)
- `POLICY_KEY` (required)
- `OUTPUT_BUCKET` (optional) - For Custodian output logs
- `NOTIFICATION_QUEUE_URL` (optional) - For notifications

---

### 4. Dockerfile (`ecs-worker/Dockerfile`)

**No changes required** - Already configured for single execution:
```dockerfile
CMD ["python", "worker.py"]
```

The container runs `worker.py`, processes one finding, and exits. ECS automatically cleans up the task.

---

## Execution Flow

### End-to-End Process:

1. **Security Finding Generated**
   - Source: SecurityHub, GuardDuty, CloudTrail, Config, Macie, etc.
   - Captured by EventBridge rule

2. **Lambda Triggered**
   ```
   EventBridge → Lambda (invoker_lambda.py)
   ```
   - Parses finding from EventBridge event
   - Extracts: source, resource_type, finding_type, severity
   - Dynamically selects policy via `config/policy-mappings.json`

3. **ECS Task Launched**
   ```python
   response = ecs.run_task(
       cluster=ECS_CLUSTER,
       taskDefinition=ECS_TASK_DEFINITION,
       launchType='FARGATE',
       networkConfiguration={...},
       overrides={
           'containerOverrides': [{
               'name': 'custodian-worker',
               'environment': [
                   {'name': 'FINDING_PAYLOAD', 'value': json.dumps(payload)},
                   {'name': 'POLICY_KEY', 'value': 'policies/s3-createbucket.yml'}
               ]
           }]
       }
   )
   ```

4. **Worker Executes**
   - Container starts on Fargate
   - Reads `FINDING_PAYLOAD` from environment
   - Downloads policy from S3
   - Executes Cloud Custodian: `custodian run -s /tmp/output policy.yml`
   - Publishes CloudWatch metrics
   - Exits (container terminates)

5. **Cleanup**
   - ECS automatically stops the task
   - Container removed
   - No ongoing costs

---

## Cost Comparison

### Before (Continuous ECS Service):
- **Always running:** 1 Fargate task × 0.25 vCPU × 0.5 GB RAM
- **Monthly cost:** ~$10-15/month (even with zero events)
- **NAT Gateway data transfer:** Continuous polling traffic

### After (On-Demand Tasks):
- **Idle cost:** $0 (no running tasks)
- **Per-event cost:** ~$0.0001-0.0005 per finding (1-5 minute execution)
- **Example:** 1000 findings/month × 2 min avg = ~$0.50/month
- **Savings:** ~95%+ for typical workloads

---

## Testing Recommendations

### 1. Manual Lambda Test
Create test event for Lambda:
```json
{
  "source": "aws.s3",
  "detail-type": "AWS API Call via CloudTrail",
  "detail": {
    "eventName": "CreateBucket",
    "requestParameters": {
      "bucketName": "test-security-bucket-123"
    }
  },
  "account": "123456789012",
  "region": "us-east-1",
  "time": "2024-01-15T10:30:00Z"
}
```

Expected outcome:
- Lambda logs show policy selection
- Lambda invokes ECS task
- Returns task ARN
- Check ECS console for task execution

### 2. Monitor ECS Task
```bash
# List running tasks
aws ecs list-tasks --cluster <cluster-name>

# Describe task
aws ecs describe-tasks --cluster <cluster-name> --tasks <task-arn>

# View logs
aws logs tail /aws/ecs/custodian-worker --follow
```

### 3. Verify CloudWatch Metrics
- `CloudCustodian/PolicyExecution/ExecutionTime`
- `CloudCustodian/PolicyExecution/ResourcesProcessed`
- `CloudCustodian/PolicyExecution/ActionsTaken`

---

## Migration Notes

### Optional: Keep SQS as Backup
If you want to retain SQS for DLQ or buffering:
1. Keep SQS resources in Terraform
2. Lambda can still send to SQS on ECS task launch failure
3. Worker can fall back to SQS polling if needed

### Rollback Plan
If issues arise, revert by:
1. Re-add `aws_ecs_service.worker` resource
2. Update Lambda to use `sqs.send_message()` instead of `ecs.run_task()`
3. Revert `worker.py` to SQS polling loop

---

## Next Steps

1. **Deploy Infrastructure**
   ```bash
   cd terraform
   terraform plan
   terraform apply
   ```

2. **Build and Push Docker Image**
   ```bash
   cd ecs-worker
   docker build -t custodian-worker .
   aws ecr get-login-password | docker login --username AWS --password-stdin <ecr-repo>
   docker tag custodian-worker:latest <ecr-repo>:latest
   docker push <ecr-repo>:latest
   ```

3. **Update Lambda Deployment Package**
   ```bash
   cd lambda
   zip -r lambda-function.zip invoker_lambda.py
   aws lambda update-function-code --function-name <function-name> --zip-file fileb://lambda-function.zip
   ```

4. **Test with Sample Event**
   - Use Lambda console or AWS CLI to invoke with test event
   - Monitor ECS task execution in CloudWatch Logs

5. **Monitor Production**
   - Set CloudWatch alarms for failed tasks
   - Monitor ECS task count (should spike on events, return to 0)
   - Track costs in Cost Explorer (ECS Fargate usage)

---

## Architecture Benefits Summary

✅ **Cost-Efficient:** Pay only for actual execution time  
✅ **Auto-Scaling:** One task per finding, scales to thousands  
✅ **Simpler:** No queue management or polling logic  
✅ **Faster Response:** Direct execution (no queue delay)  
✅ **Better Isolation:** Each finding processed in isolated container  
✅ **Easy Debugging:** Each task has dedicated CloudWatch log stream  

---

## File Changes Summary

| File | Status | Changes |
|------|--------|---------|
| `terraform/unified-event-driven.tf` | ✅ Modified | Removed ECS Service, added ECS permissions to Lambda, added ECS env vars |
| `lambda/invoker_lambda.py` | ✅ Modified | Added `trigger_ecs_task()`, replaced SQS with ECS `run_task()` |
| `ecs-worker/worker.py` | ✅ Modified | Changed from polling loop to single-execution mode |
| `ecs-worker/Dockerfile` | ✅ No Change | Already compatible with single-execution |
| `config/policy-mappings.json` | ✅ No Change | Dynamic policy selection still works |
| `policies/s3-createbucket.yml` | ✅ No Change | Policy files unchanged |

---

## Configuration Reference

### Lambda Environment Variables
```
POLICY_BUCKET          = S3 bucket with policies
POLICY_MAPPING_KEY     = config/policy-mappings.json
DEFAULT_POLICY_KEY     = policies/s3-createbucket.yml
ENABLE_ENRICHMENT      = true
ECS_CLUSTER            = custodian-cluster
ECS_TASK_DEFINITION    = custodian-worker
ECS_SUBNETS            = subnet-xxx,subnet-yyy
ECS_SECURITY_GROUP     = sg-zzz
```

### ECS Task Environment Variables (set by Lambda)
```
FINDING_PAYLOAD        = {"finding": {...}, "policy_key": "..."}
POLICY_BUCKET          = S3 bucket
POLICY_KEY             = policies/s3-createbucket.yml
OUTPUT_BUCKET          = (optional) S3 bucket for Custodian output
NOTIFICATION_QUEUE_URL = (optional) SQS queue for notifications
```

---

**Status:** ✅ Ready for deployment  
**Risk Level:** Low (easily reversible)  
**Cost Impact:** 90-95% reduction for typical workloads
