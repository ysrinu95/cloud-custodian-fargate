# Deployment Checklist - Event-Driven Architecture

## Pre-Deployment Verification

### 1. Code Review ✅
- [x] Lambda code updated to use `ecs.run_task()`
- [x] Worker code updated for single-execution mode
- [x] Terraform infrastructure updated (ECS Service removed)
- [x] All syntax errors resolved

### 2. Configuration Files
- [x] `config/policy-mappings.json` exists
- [x] `policies/s3-createbucket.yml` exists
- [ ] Update `terraform/terraform.tfvars` with your values:
  ```hcl
  aws_region          = "us-east-1"
  environment         = "production"
  vpc_cidr            = "10.0.0.0/16"
  availability_zones  = ["us-east-1a", "us-east-1b"]
  ```

---

## Deployment Steps

### Step 1: Terraform Infrastructure
```bash
cd terraform

# Initialize (if first time)
terraform init

# Review changes
terraform plan

# Apply infrastructure changes
terraform apply

# Note down outputs:
# - ECR repository URL
# - Lambda function name
# - ECS cluster name
# - ECS task definition name
```

**Expected Changes:**
- ❌ Remove: `aws_ecs_service.worker`
- ✅ Update: Lambda IAM role (add ECS permissions)
- ✅ Update: Lambda environment variables (add ECS config)

---

### Step 2: Build and Push Docker Image
```bash
cd ecs-worker

# Get ECR login
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build image
docker build -t custodian-worker:latest .

# Tag for ECR (use repository URL from Terraform output)
docker tag custodian-worker:latest <ecr-repo-url>:latest

# Push to ECR
docker push <ecr-repo-url>:latest
```

**Verification:**
```bash
# List images in ECR
aws ecr describe-images --repository-name custodian-worker --region us-east-1
```

---

### Step 3: Package and Deploy Lambda
```bash
cd lambda

# Create deployment package
zip -r lambda-function.zip invoker_lambda.py

# Upload to Lambda (use function name from Terraform output)
aws lambda update-function-code \
  --function-name <lambda-function-name> \
  --zip-file fileb://lambda-function.zip \
  --region us-east-1

# Wait for update to complete
aws lambda wait function-updated \
  --function-name <lambda-function-name> \
  --region us-east-1
```

**Verification:**
```bash
# Check function configuration
aws lambda get-function-configuration \
  --function-name <lambda-function-name> \
  --region us-east-1
```

---

### Step 4: Upload Policy Files to S3
```bash
# Upload policy mappings
aws s3 cp config/policy-mappings.json s3://<policy-bucket>/config/policy-mappings.json

# Upload policy file
aws s3 cp policies/s3-createbucket.yml s3://<policy-bucket>/policies/s3-createbucket.yml

# Verify uploads
aws s3 ls s3://<policy-bucket>/config/
aws s3 ls s3://<policy-bucket>/policies/
```

---

### Step 5: Test Lambda Function

#### Create test event file: `test-event.json`
```json
{
  "source": "aws.s3",
  "detail-type": "AWS API Call via CloudTrail",
  "detail": {
    "eventName": "CreateBucket",
    "eventTime": "2024-01-15T10:30:00Z",
    "eventSource": "s3.amazonaws.com",
    "requestParameters": {
      "bucketName": "test-security-bucket-12345"
    },
    "resources": [
      {
        "ARN": "arn:aws:s3:::test-security-bucket-12345"
      }
    ]
  },
  "account": "123456789012",
  "region": "us-east-1",
  "time": "2024-01-15T10:30:00Z"
}
```

#### Invoke Lambda
```bash
# Invoke Lambda with test event
aws lambda invoke \
  --function-name <lambda-function-name> \
  --payload file://test-event.json \
  --region us-east-1 \
  response.json

# Check response
cat response.json
```

**Expected Response:**
```json
{
  "statusCode": 200,
  "body": "{\"task_arn\": \"arn:aws:ecs:us-east-1:...\", \"finding_id\": \"...\", \"policy_key\": \"policies/s3-createbucket.yml\"}"
}
```

---

### Step 6: Monitor ECS Task Execution

#### Check running tasks
```bash
# List tasks (should see 1 task running)
aws ecs list-tasks \
  --cluster <cluster-name> \
  --region us-east-1

# Get task details
aws ecs describe-tasks \
  --cluster <cluster-name> \
  --tasks <task-arn> \
  --region us-east-1
```

#### View logs
```bash
# Tail CloudWatch logs
aws logs tail /aws/ecs/custodian-worker --follow --region us-east-1
```

**Expected Log Output:**
```
Starting Fargate worker (single-execution mode) at 2024-01-15T10:30:05Z
Parsing finding payload...
Finding ID: CreateBucket-...
Source: aws.s3
Resource Type: S3
Severity: HIGH
Processing finding:
  Downloading policy from S3...
  ✓ Policy downloaded: /tmp/policies/policy.yml
  Executing: custodian run -s /tmp/output ...
  ✓ Policy executed successfully (3.45s)
  Resources processed: 1
  Actions taken: 1

✓ Successfully processed finding CreateBucket-...
```

---

### Step 7: Verify Task Cleanup

Wait 1-2 minutes after task completes, then check:
```bash
# Should return empty list (tasks auto-terminated)
aws ecs list-tasks --cluster <cluster-name> --region us-east-1
```

**✅ Expected:** No running tasks (task count = 0)

---

## Post-Deployment Verification

### 1. Lambda Function
- [ ] Function environment variables include:
  - `ECS_CLUSTER`
  - `ECS_TASK_DEFINITION`
  - `ECS_SUBNETS`
  - `ECS_SECURITY_GROUP`
- [ ] Function IAM role has permissions:
  - `ecs:RunTask`
  - `ecs:DescribeTasks`
  - `iam:PassRole`

### 2. ECS Infrastructure
- [ ] ECS Cluster exists
- [ ] ECS Task Definition exists (no Service)
- [ ] Task Definition uses Fargate launch type
- [ ] Task Definition has correct image URI

### 3. CloudWatch Metrics
Check metrics in CloudWatch console:
- [ ] `CloudCustodian/PolicyExecution/ExecutionTime`
- [ ] `CloudCustodian/PolicyExecution/ResourcesProcessed`
- [ ] `CloudCustodian/PolicyExecution/ActionsTaken`

### 4. S3 Buckets
- [ ] Policy bucket has `config/policy-mappings.json`
- [ ] Policy bucket has `policies/s3-createbucket.yml`
- [ ] Output bucket exists (optional)

### 5. EventBridge Rules
```bash
# List rules
aws events list-rules --region us-east-1 | grep custodian

# Check rule targets
aws events list-targets-by-rule --rule unified-security-findings --region us-east-1
```

---

## Monitoring Commands

### View recent Lambda invocations
```bash
aws logs tail /aws/lambda/<function-name> --follow --region us-east-1
```

### View ECS task history
```bash
aws ecs list-tasks \
  --cluster <cluster-name> \
  --desired-status STOPPED \
  --max-results 10 \
  --region us-east-1
```

### Check CloudWatch metrics
```bash
aws cloudwatch get-metric-statistics \
  --namespace CloudCustodian/PolicyExecution \
  --metric-name ExecutionTime \
  --start-time 2024-01-15T00:00:00Z \
  --end-time 2024-01-15T23:59:59Z \
  --period 3600 \
  --statistics Average,Maximum \
  --region us-east-1
```

---

## Troubleshooting

### Issue: Lambda returns 500 error
**Check:**
1. Lambda logs: `aws logs tail /aws/lambda/<function-name> --region us-east-1`
2. IAM permissions: Verify `ecs:RunTask` and `iam:PassRole`
3. Environment variables: Verify ECS cluster/task definition names

### Issue: ECS task fails to start
**Check:**
1. Task Definition: `aws ecs describe-task-definition --task-definition <name>`
2. ECR image exists: `aws ecr describe-images --repository-name custodian-worker`
3. Network configuration: Verify subnets and security groups
4. Task execution role has ECR pull permissions

### Issue: Worker exits with error
**Check:**
1. CloudWatch logs: `aws logs tail /aws/ecs/custodian-worker --region us-east-1`
2. Environment variables passed to container
3. S3 policy file exists and is accessible
4. Task role has S3 read permissions

### Issue: Policy doesn't execute
**Check:**
1. Policy file syntax: `custodian validate policies/s3-createbucket.yml`
2. Environment variables: `RESOURCE_ID`, `FINDING_ID` set correctly
3. Worker logs for download/execution errors

---

## Rollback Procedure

If issues arise, revert to SQS-based architecture:

1. **Restore ECS Service in Terraform:**
   ```bash
   git checkout HEAD~1 terraform/unified-event-driven.tf
   terraform apply
   ```

2. **Revert Lambda code:**
   ```bash
   git checkout HEAD~1 lambda/invoker_lambda.py
   cd lambda
   zip -r lambda-function.zip invoker_lambda.py
   aws lambda update-function-code --function-name <name> --zip-file fileb://lambda-function.zip
   ```

3. **Revert Worker code:**
   ```bash
   git checkout HEAD~1 ecs-worker/worker.py
   cd ecs-worker
   docker build -t custodian-worker .
   docker push <ecr-repo>:latest
   ```

4. **Update ECS Service:**
   ```bash
   aws ecs update-service --cluster <cluster> --service worker --force-new-deployment
   ```

---

## Cost Monitoring

### Set up billing alert
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name ecs-fargate-cost-alert \
  --alarm-description "Alert when ECS Fargate costs exceed $10" \
  --metric-name EstimatedCharges \
  --namespace AWS/Billing \
  --statistic Maximum \
  --period 86400 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 1 \
  --region us-east-1
```

### Check daily costs
- AWS Cost Explorer → Filter by Service: ECS
- Compare costs before/after deployment
- Expected: 90-95% reduction

---

## Success Criteria

✅ Lambda invokes successfully (200 response)  
✅ ECS task starts and completes  
✅ Worker logs show policy execution  
✅ Task terminates after completion  
✅ No running tasks when idle  
✅ CloudWatch metrics published  
✅ Cost reduced to near-zero when idle

---

**Deployment Status:** Ready ✅  
**Estimated Deployment Time:** 30-45 minutes  
**Risk Level:** Low (easily reversible)
