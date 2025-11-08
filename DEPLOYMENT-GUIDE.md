# Step-by-Step Deployment Guide

This guide walks you through deploying the unified event-driven Cloud Custodian framework from scratch.

## 📋 Prerequisites Checklist

Before starting, ensure you have:

- [ ] AWS Account with admin access
- [ ] GitHub repository created
- [ ] AWS CLI installed and configured locally
- [ ] Terraform installed (v1.6+)
- [ ] Docker installed (for local testing)
- [ ] Python 3.11+ installed

## Phase 1: Bootstrap (One-Time Setup)

### Step 1.1: Create Terraform State Bucket

```bash
# Set your AWS account ID and region
export AWS_ACCOUNT_ID="your-account-id"
export AWS_REGION="us-east-1"
export STATE_BUCKET="your-terraform-state-bucket"

# Create S3 bucket for Terraform state
aws s3api create-bucket \
  --bucket "${STATE_BUCKET}" \
  --region ${AWS_REGION}

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket "${STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
  --bucket "${STATE_BUCKET}" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Block public access
aws s3api put-public-access-block \
  --bucket "${STATE_BUCKET}" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### Step 1.2: Configure GitHub OIDC

```bash
# Deploy bootstrap infrastructure (OIDC provider + IAM roles)
cd terraform

# Initialize Terraform
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="key=terraform/cloud-custodian/bootstrap.tfstate" \
  -backend-config="region=${AWS_REGION}"

# Review plan
terraform plan

# Apply (creates OIDC provider and GitHub Actions role)
terraform apply -auto-approve

# Get the role ARN
export GITHUB_ROLE_ARN=$(terraform output -raw github_actions_role_arn)
echo "GitHub Actions Role: ${GITHUB_ROLE_ARN}"
```

### Step 1.3: Configure GitHub Secrets

Go to your GitHub repository → Settings → Secrets and variables → Actions

Add the following secrets:

| Secret Name | Value | Description |
|------------|-------|-------------|
| `AWS_ACCOUNT_ID` | Your AWS account ID | Used for IAM role ARN |
| `TERRAFORM_STATE_BUCKET` | Your state bucket name | S3 backend for Terraform |

## Phase 2: Deploy Unified Infrastructure

### Step 2.1: Prepare Lambda Package

```bash
# From repository root
cd lambda

# Install dependencies
pip install -r requirements.txt -t .

# Create deployment package
zip -r lambda-function.zip .

# Verify package
ls -lh lambda-function.zip

cd ..
```

### Step 2.2: Deploy via GitHub Actions (Recommended)

1. **Commit and Push Changes**:
   ```bash
   git add .
   git commit -m "feat: Add unified event-driven architecture"
   git push origin main
   ```

2. **Monitor Deployment**:
   - Go to GitHub Actions tab
   - Watch "Deploy Unified Infrastructure" workflow
   - Review logs for each step

3. **Verify Deployment**:
   - Check AWS Console for resources created
   - Look for S3 bucket, SQS queue, Lambda function, ECS cluster

### Step 2.3: Deploy Manually (Alternative)

```bash
cd terraform

# Initialize Terraform
terraform init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="key=cloud-custodian/unified/terraform.tfstate" \
  -backend-config="region=${AWS_REGION}"

# Plan deployment
terraform plan \
  -var="aws_region=${AWS_REGION}" \
  -var="environment=production" \
  -var="enable_ecs_worker=true" \
  -out=tfplan

# Apply
terraform apply tfplan

# Get outputs
terraform output
```

### Step 2.4: Upload Policies to S3

```bash
# Get bucket name from Terraform output
export S3_BUCKET=$(terraform -chdir=terraform output -raw s3_bucket_name)

# Upload policies
aws s3 sync policies/ "s3://${S3_BUCKET}/policies/" \
  --exclude "*" --include "*.yml" --delete

# Upload config
aws s3 cp config/policy-mappings.yml "s3://${S3_BUCKET}/config/policy-mappings.yml"

# Verify uploads
aws s3 ls "s3://${S3_BUCKET}/policies/"
aws s3 ls "s3://${S3_BUCKET}/config/"
```

### Step 2.5: Build and Push ECS Worker Image

```bash
# Get ECR repository URL
export ECR_REPO=$(terraform -chdir=terraform output -raw ecr_repository_url)

# Login to ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin "${ECR_REPO}"

# Build image
cd ecs-worker
docker build -t cloud-custodian-worker:latest .

# Tag image
docker tag cloud-custodian-worker:latest "${ECR_REPO}:latest"

# Push image
docker push "${ECR_REPO}:latest"

# Verify push
aws ecr describe-images --repository-name cloud-custodian-worker --region ${AWS_REGION}
```

### Step 2.6: Start ECS Service

```bash
# Get ECS cluster and service names
export ECS_CLUSTER=$(terraform -chdir=terraform output -raw ecs_cluster_name)
export ECS_SERVICE=$(terraform -chdir=terraform output -raw ecs_service_name)

# Force new deployment (to pull latest image)
aws ecs update-service \
  --cluster "${ECS_CLUSTER}" \
  --service "${ECS_SERVICE}" \
  --force-new-deployment \
  --region ${AWS_REGION}

# Check service status
aws ecs describe-services \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}" \
  --region ${AWS_REGION} \
  --query 'services[0].{Status:status,Running:runningCount,Desired:desiredCount}'
```

## Phase 3: Test with One Policy

### Step 3.1: Enable CloudTrail (if not enabled)

```bash
# Check if CloudTrail is logging S3 events
aws cloudtrail get-event-selectors --trail-name <your-trail-name>

# If not, enable data events for S3
aws cloudtrail put-event-selectors \
  --trail-name <your-trail-name> \
  --event-selectors '[{
    "ReadWriteType": "WriteOnly",
    "IncludeManagementEvents": true,
    "DataResources": [{
      "Type": "AWS::S3::Object",
      "Values": ["arn:aws:s3:::*/*"]
    }]
  }]'
```

### Step 3.2: Verify Policy Mapping

```bash
# Check policy mappings
aws s3 cp "s3://${S3_BUCKET}/config/policy-mappings.yml" - | grep -A 5 "s3-createbucket"
```

### Step 3.3: Create Test S3 Bucket (Triggers Policy)

```bash
# Create a test bucket (this will trigger the EventBridge rule)
export TEST_BUCKET="test-custodian-$(date +%s)"

aws s3api create-bucket \
  --bucket "${TEST_BUCKET}" \
  --region ${AWS_REGION}

echo "Test bucket created: ${TEST_BUCKET}"
echo "Waiting 60 seconds for processing..."
sleep 60

# Check if bucket still exists (should be deleted by policy)
if aws s3api head-bucket --bucket "${TEST_BUCKET}" 2>/dev/null; then
  echo "⚠️ Bucket still exists (policy may not have run yet)"
else
  echo "✅ Bucket was deleted by Cloud Custodian policy!"
fi
```

### Step 3.4: Monitor Logs

```bash
# Lambda Invoker logs
aws logs tail /aws/lambda/cloud-custodian-invoker \
  --since 5m \
  --format short \
  --follow

# ECS Worker logs (in another terminal)
aws logs tail /ecs/cloud-custodian-worker \
  --since 5m \
  --format short \
  --follow
```

### Step 3.5: Check SQS Queue

```bash
# Get queue URL
export QUEUE_URL=$(terraform -chdir=terraform output -raw sqs_queue_url)

# Check queue attributes
aws sqs get-queue-attributes \
  --queue-url "${QUEUE_URL}" \
  --attribute-names All \
  --query 'Attributes.{MessagesAvailable:ApproximateNumberOfMessages,MessagesInFlight:ApproximateNumberOfMessagesNotVisible,MessagesDLQ:ApproximateNumberOfMessagesDelayed}'
```

## Phase 4: Add More Policies

### Step 4.1: Test Policy Locally (Dry Run)

```bash
# Install Cloud Custodian
pip install c7n

# Test policy syntax
custodian validate policies/ec2-runinstances.yml

# Dry run (no actions)
custodian run \
  -s output/ \
  --region ${AWS_REGION} \
  --dryrun \
  policies/ec2-runinstances.yml

# Review results
cat output/ec2-terminate-public-instances/resources.json
```

### Step 4.2: Update Policy Mappings

Edit `config/policy-mappings.yml`:

```yaml
mappings:
  # Add new mapping
  - source: ec2
    resource_type: EC2
    finding_type: "RunInstances"
    policy_file: "ec2-runinstances.yml"
```

### Step 4.3: Deploy Policy

```bash
# Upload new policy
aws s3 cp policies/ec2-runinstances.yml "s3://${S3_BUCKET}/policies/ec2-runinstances.yml"

# Upload updated mappings
aws s3 cp config/policy-mappings.yml "s3://${S3_BUCKET}/config/policy-mappings.yml"

# Restart ECS service to reload config
aws ecs update-service \
  --cluster "${ECS_CLUSTER}" \
  --service "${ECS_SERVICE}" \
  --force-new-deployment \
  --region ${AWS_REGION}
```

### Step 4.4: Test New Policy

```bash
# For EC2 policy - launch a test instance
aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \
  --instance-type t2.micro \
  --count 1 \
  --associate-public-ip-address \
  --region ${AWS_REGION}

# Monitor logs for execution
aws logs tail /ecs/cloud-custodian-worker --since 2m --follow
```

## Phase 5: Production Readiness

### Step 5.1: Configure Monitoring

```bash
# Create CloudWatch dashboard
aws cloudwatch put-dashboard \
  --dashboard-name CloudCustodian \
  --dashboard-body file://monitoring/dashboard.json

# Create SNS topic for alerts
aws sns create-topic --name cloud-custodian-alerts

# Subscribe to topic
aws sns subscribe \
  --topic-arn arn:aws:sns:${AWS_REGION}:${AWS_ACCOUNT_ID}:cloud-custodian-alerts \
  --protocol email \
  --notification-endpoint your-email@example.com
```

### Step 5.2: Enable DLQ Monitoring

```bash
# Create alarm for DLQ messages
aws cloudwatch put-metric-alarm \
  --alarm-name cloud-custodian-dlq-messages \
  --alarm-description "Alert when messages appear in DLQ" \
  --metric-name ApproximateNumberOfMessagesVisible \
  --namespace AWS/SQS \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 1 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=QueueName,Value=cloud-custodian-dlq
```

### Step 5.3: Configure Auto-Scaling (Optional)

```bash
# Enable ECS service auto-scaling based on SQS queue depth
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/${ECS_CLUSTER}/${ECS_SERVICE} \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 1 \
  --max-capacity 10

# Create scaling policy
aws application-autoscaling put-scaling-policy \
  --service-namespace ecs \
  --resource-id service/${ECS_CLUSTER}/${ECS_SERVICE} \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-name scale-on-queue-depth \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration file://autoscaling-policy.json
```

## 🎉 Deployment Complete!

Your unified event-driven Cloud Custodian framework is now running!

### Quick Health Check

```bash
# Check Lambda
aws lambda get-function --function-name cloud-custodian-invoker

# Check ECS service
aws ecs describe-services \
  --cluster ${ECS_CLUSTER} \
  --services ${ECS_SERVICE}

# Check EventBridge rules
aws events list-rules --name-prefix cloud-custodian

# Check S3 bucket
aws s3 ls s3://${S3_BUCKET}/
```

### Next Steps

1. ✅ Monitor CloudWatch Logs for 24 hours
2. ✅ Add more policies incrementally
3. ✅ Configure alerting for critical findings
4. ✅ Set up regular policy reviews
5. ✅ Document custom policies

---

## 🆘 Troubleshooting

### Lambda Not Triggering
- Check EventBridge rule is enabled
- Verify CloudTrail is logging events
- Check Lambda permissions

### ECS Task Not Starting
- Verify ECR image exists
- Check ECS task role permissions
- Review CloudWatch Logs for errors

### Policy Not Executing
- Verify policy uploaded to S3
- Check policy-mappings.yml syntax
- Ensure resource IDs are correct

### Messages in DLQ
- Check CloudWatch Logs for errors
- Validate policy syntax
- Verify AWS permissions

---

**For detailed architecture info, see:** `README-UNIFIED.md`  
**For cleanup instructions, see:** `CLEANUP.md`
