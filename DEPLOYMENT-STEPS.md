# Deployment Steps - S3 Validator Implementation

## What Changed

✅ **Disabled CloudTrail-based EventBridge rules** (S3/EC2 events)  
✅ **Enabled Security Hub-only triggers** (actual security findings)  
✅ **Added S3 bucket validator** (checks if bucket is actually public)  
✅ **Updated Lambda IAM permissions** (S3 read access for validation)

---

## Current Architecture

```
Security Hub Finding → EventBridge → Lambda
                                       ↓
                                 Parse Finding
                                       ↓
                        ┌────── S3 Validator (if S3)
                        │             ↓
                        │    Bucket is public?
                        │        ↙        ↘
                        │     YES          NO
                        │      ↓            ↓
                        └→ Queue SQS    Skip (log)
                               ↓
                      ECS Auto-scales & Remediates
```

**Validation Logic:**
- **S3 resources:** Validated (checks if public before queueing)
- **EC2/IAM/other:** Skip validation (always queue for remediation)

---

## Deploy Steps

### Step 1: Package Lambda Function

```powershell
cd lambda
.\package.ps1
```

This creates `lambda-function.zip` with:
- `invoker_lambda.py`
- `validators/` (S3 validator enabled)

### Step 2: Review Terraform Changes

```powershell
cd ..\terraform
terraform plan
```

**Expected changes:**
- ❌ Destroy: `aws_cloudwatch_event_rule.s3_events` (commented out)
- ❌ Destroy: `aws_cloudwatch_event_rule.ec2_events` (commented out)
- ⚙️ Update: `aws_lambda_function.invoker` (new code, IAM permissions)
- ✅ Keep: `aws_cloudwatch_event_rule.unified_security` (active)

### Step 3: Apply Changes

```powershell
terraform apply
```

Type `yes` to confirm.

### Step 4: Verify Deployment

```powershell
# Check Lambda function updated
aws lambda get-function --function-name cloud-custodian-invoker --region us-east-1 --query 'Configuration.[FunctionName,LastModified,Runtime]'

# Check EventBridge rules
aws events list-rules --region us-east-1 --query 'Rules[?contains(Name, `custodian`)].Name'

# Should show only: cloud-custodian-unified-security
```

---

## Testing

### Test 1: Create Private S3 Bucket (Should Skip)

```powershell
# Create bucket with public access blocked
aws s3api create-bucket --bucket test-private-bucket-$(Get-Random) --region us-east-1
aws s3api put-public-access-block --bucket test-private-bucket-* --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

**Expected:** Lambda validates → Bucket is private → Skip SQS → No containers spin up

### Test 2: Create Public S3 Bucket (Should Remediate)

```powershell
# Create bucket without public access block
aws s3api create-bucket --bucket test-public-bucket-$(Get-Random) --region us-east-1
```

**Expected:** Lambda validates → Bucket is public → Queue to SQS → ECS scales up → Policy executes

### Test 3: Check Lambda Logs

```powershell
aws logs tail /aws/lambda/cloud-custodian-invoker --follow --region us-east-1
```

**Look for:**
- `✓ Validation passed: Bucket 'xxx' has public access - remediation required`
- `⊗ Validation failed: Bucket 'xxx' is not public - no remediation needed`
- `⊗ Skipping SQS queueing - no remediation required`

### Test 4: Monitor SQS Queue

```powershell
aws sqs get-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/172327596604/cloud-custodian-queue --attribute-names ApproximateNumberOfMessages --region us-east-1
```

**Expected:**
- Private bucket → `ApproximateNumberOfMessages: 0` (not queued)
- Public bucket → `ApproximateNumberOfMessages: 1+` (queued)

### Test 5: Check ECS Service Scaling

```powershell
aws ecs describe-services --cluster cloud-custodian-cluster --services cloud-custodian-worker-production --region us-east-1 --query 'services[0].[desiredCount,runningCount]'
```

**Expected:**
- No public buckets → `[0, 0]` (scaled to zero)
- Public bucket created → `[1+, 1+]` (scaled up)

---

## Monitoring

### CloudWatch Metrics

```powershell
# View findings received
aws cloudwatch get-metric-statistics --namespace CloudCustodian/SecurityFindings --metric-name FindingsReceived --start-time $(Get-Date).AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ss") --end-time $(Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") --period 3600 --statistics Sum --region us-east-1

# View findings skipped (validation failed)
aws cloudwatch get-metric-statistics --namespace CloudCustodian/SecurityFindings --metric-name FindingsSkipped --start-time $(Get-Date).AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ss") --end-time $(Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") --period 3600 --statistics Sum --region us-east-1

# View findings queued (validation passed)
aws cloudwatch get-metric-statistics --namespace CloudCustodian/SecurityFindings --metric-name FindingsQueued --start-time $(Get-Date).AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ss") --end-time $(Get-Date).ToString("yyyy-MM-ddTHH:mm:ss") --period 3600 --statistics Sum --region us-east-1
```

---

## Enabling EC2/IAM Validators Later

If you want to enable EC2 or IAM validation later:

### Step 1: Uncomment Validators

Edit `lambda/validators/validator_factory.py`:

```python
_validators = {
    'S3': S3Validator,
    'EC2': EC2Validator,  # Uncomment this
    'IAM': IAMValidator,  # Uncomment this
}
```

Also uncomment imports at top:

```python
from .ec2_validator import EC2Validator
from .iam_validator import IAMValidator
```

### Step 2: Add IAM Permissions

Edit `terraform/unified-event-driven.tf`, add to Lambda IAM policy:

```hcl
# For EC2 Validator
{
  Effect = "Allow"
  Action = [
    "ec2:DescribeInstances",
    "ec2:DescribeSecurityGroups"
  ]
  Resource = "*"
}

# For IAM Validator
{
  Effect = "Allow"
  Action = [
    "iam:GetUser",
    "iam:GetRole",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:ListAttachedUserPolicies",
    "iam:ListAttachedRolePolicies",
    "iam:ListUserPolicies",
    "iam:ListAccessKeys",
    "iam:ListMFADevices"
  ]
  Resource = "*"
}
```

### Step 3: Repackage and Deploy

```powershell
cd lambda
.\package.ps1

cd ..\terraform
terraform apply
```

---

## Rollback

If you need to rollback:

```powershell
cd terraform
git checkout unified-event-driven.tf

cd ..\lambda
git checkout invoker_lambda.py
rm -rf validators/

terraform apply
```

---

## Cost Comparison

### Before Validator (All Events Trigger Containers)

- 1000 S3 API calls/month
- All trigger Lambda → SQS → ECS
- Cost: 1000 * $0.006 = **$6.00/month**

### After Validator (Only Public Buckets Trigger)

- 1000 S3 API calls/month
- 900 are private (validation skips)
- 100 are public (validation passes)
- Cost: 100 * $0.006 + 900 * $0.0000002 = **$0.60/month**

**Savings: $5.40/month (90% reduction)**

---

## Support

- Lambda logs: `/aws/lambda/cloud-custodian-invoker`
- ECS logs: `/ecs/cloud-custodian-worker`
- Validators README: `lambda/VALIDATORS-README.md`
