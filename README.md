# Cloud Custodian - Unified Event-Driven Architecture

> **Architecture Pattern**: Single EventBridge Rule → Lambda Invoker → SQS → ECS Fargate Worker → S3 Outputs

This implementation follows the **Unified Event-Driven Architecture** pattern that consolidates all security event sources into a single processing pipeline.

## 🏗️ Architecture Overview

```
┌─────────────────┐
│  Event Sources  │
│  - SecurityHub  │
│  - GuardDuty    │
│  - Macie        │
│  - Config       │
│  - CloudTrail   │
│  - Analyzer     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────┐
│  EventBridge (Unified)  │
│  Single rule matching   │
│  all event sources      │
└────────┬────────────────┘
         │
         ▼
┌──────────────────────────┐
│  Lambda Invoker          │
│  - Load policy_map.json  │
│  - Match event to policy │
│  - Upload large events   │
│  - Enqueue to SQS        │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────┐
│    SQS Queue     │
│  Buffer & DLQ    │
└────────┬─────────┘
         │
         ▼
┌───────────────────────────┐
│  ECS Fargate Worker       │
│  - Poll SQS               │
│  - Fetch policy_map.yml   │
│  - Fetch policies from S3 │
│  - Fetch event (if large) │
│  - Run custodian          │
│  - Upload outputs         │
│  - Delete message         │
└────────┬──────────────────┘
         │
         ▼
┌──────────────────┐
│   S3 Bucket      │
│  - policy_map    │
│  - Policies      │
│  - Large events  │
│  - Outputs       │
└──────────────────┘
```

## ✨ Key Benefits

✅ **Single EventBridge Rule** - Not 50+ rules, just one unified pattern  
✅ **Centralized Lambda Invoker** - Policy mapping logic in one place  
✅ **SQS Buffering** - Rate limiting, DLQ, retry semantics  
✅ **Scalable Fargate Workers** - Autoscale based on queue depth  
✅ **Dynamic Policy Loading** - No image rebuild for policy changes  
✅ **Cost-Effective** - Batching, pay-per-use model

## 📁 Project Structure

```
complete-deployment/
├── terraform/
│   └── unified-event-driven.tf     # Main infrastructure definition
├── lambda/
│   ├── invoker_lambda.py            # Lambda function code
│   └── requirements.txt             # Lambda dependencies
├── ecs-worker/
│   ├── Dockerfile                   # ECS worker container
│   ├── worker.py                    # Main worker application
│   ├── policy_selector.py           # Policy selection logic
│   └── requirements.txt             # Worker dependencies
├── policies/
│   ├── s3-createbucket-simple.yml   # 👈 Start with this!
│   ├── s3-security-findings.yml
│   ├── ec2-runinstances.yml
│   └── ... (add more incrementally)
├── config/
│   └── policy-mappings.yml          # Event → Policy mappings
└── .github/workflows/
    ├── deploy-unified-infrastructure.yml  # Deployment workflow
    └── test-policies.yml                   # Testing workflow
```

## 🚀 Getting Started

### Prerequisites

1. **AWS Account** with appropriate permissions
2. **GitHub Repository** with OIDC configured
3. **Terraform State Bucket** (S3 backend)
4. **GitHub Secrets** configured:
   - `AWS_ACCOUNT_ID`
   - `TERRAFORM_STATE_BUCKET`

### Step 1: Deploy Infrastructure

```bash
# Option 1: Push to main branch (auto-deploys)
git push origin main

# Option 2: Manual deployment via GitHub Actions
# Go to Actions → Deploy Unified Infrastructure → Run workflow
```

The deployment will:
1. ✅ Package Lambda function
2. ✅ Create Terraform infrastructure (S3, SQS, Lambda, ECR, ECS)
3. ✅ Upload policies and config to S3
4. ✅ Build and push Docker image to ECR
5. ✅ Deploy ECS Fargate service

### Step 2: Test with One Policy

Start with the simple S3 CreateBucket policy:

```bash
# Via GitHub Actions
# Go to Actions → Test Policies One by One → Run workflow
# Select: s3-createbucket-simple.yml
# Test Action: deploy-test
```

This will:
1. ✅ Validate policy syntax
2. ✅ Run dry-run to see matching resources
3. ✅ Upload policy to S3
4. ✅ Create test S3 bucket (triggers EventBridge)
5. ✅ Wait for policy execution
6. ✅ Verify bucket deletion

### Step 3: Monitor Execution

Check CloudWatch Logs:
- **Lambda Invoker**: `/aws/lambda/cloud-custodian-invoker`
- **ECS Worker**: `/ecs/cloud-custodian-worker`

Check SQS Queue metrics in AWS Console.

### Step 4: Add More Policies

Once the first policy works, add more incrementally:

1. Edit `config/policy-mappings.yml` to map events to policies
2. Add policy YAML files to `policies/` directory
3. Push changes (auto-deploys)
4. Test each policy individually

## 📝 Configuration Files

### terraform/unified-event-driven.tf

Main infrastructure definition. Key variables:

```hcl
variable "enable_ecs_worker" {
  default = true  # Set to false for Lambda-only approach
}

variable "ecs_task_cpu" {
  default = "512"  # 0.5 vCPU
}

variable "ecs_task_memory" {
  default = "1024"  # 1 GB
}
```

### config/policy-mappings.yml

Maps security events to Cloud Custodian policies:

```yaml
version: "1.0"
s3_bucket: "cloud-custodian-unified-<account-id>"
s3_prefix: "policies"

mappings:
  - source: s3
    resource_type: S3
    finding_type: "createbucket"
    policy_file: "s3-createbucket-simple.yml"
```

### policies/s3-createbucket-simple.yml

Example policy (start here):

```yaml
policies:
  - name: s3-delete-unprotected-buckets
    resource: aws.s3
    description: Delete S3 buckets without public access block
    
    filters:
      - type: bucket-public-access-block
        BlockPublicAcls: false
    
    actions:
      - type: delete
        remove-contents: true
```

## 🧪 Testing Strategy

### Phase 1: Validate Syntax
```bash
custodian validate policies/s3-createbucket-simple.yml
```

### Phase 2: Dry Run
```bash
custodian run \
  -s output/ \
  --region us-east-1 \
  --dryrun \
  policies/s3-createbucket-simple.yml
```

### Phase 3: Deploy & Test
Use GitHub Actions workflow to:
1. Upload policy to S3
2. Trigger test event
3. Monitor execution
4. Verify results

## 📊 Monitoring & Debugging

### CloudWatch Logs

**Lambda Invoker Logs:**
```bash
aws logs tail /aws/lambda/cloud-custodian-invoker --follow
```

**ECS Worker Logs:**
```bash
aws logs tail /ecs/cloud-custodian-worker --follow
```

### CloudWatch Metrics

Custom metrics published:
- `CloudCustodian/SecurityFindings` - Findings received/queued
- `CloudCustodian/FargateWorker` - Messages processed
- `CloudCustodian/PolicyExecution` - Policy execution metrics

### SQS Queue Monitoring

```bash
aws sqs get-queue-attributes \
  --queue-url <queue-url> \
  --attribute-names All
```

Key metrics:
- `ApproximateNumberOfMessages` - Messages waiting
- `ApproximateNumberOfMessagesNotVisible` - Messages being processed
- `ApproximateNumberOfMessagesDelayed` - Messages delayed

## 🔧 Troubleshooting

### Issue: Lambda times out
**Solution**: Increase Lambda timeout in Terraform (current: 60s)

### Issue: ECS task fails to start
**Solution**: Check ECR image exists and ECS task role has permissions

### Issue: Messages stuck in DLQ
**Solution**: Check CloudWatch Logs for error details, verify policy syntax

### Issue: Policy not executing
**Solution**: 
1. Verify EventBridge rule is enabled
2. Check Lambda invocation metrics
3. Verify SQS queue has messages
4. Check ECS service is running

## � Repository Structure

```
complete-deployment/
├── .github/workflows/              # CI/CD pipelines
│   ├── deploy-unified-infrastructure.yml
│   └── test-policies.yml
├── terraform/                      # Infrastructure as Code
│   ├── unified-event-driven.tf    # Single file for all resources
│   └── terraform.tfvars           # Configuration values
├── lambda/                         # Event processor
│   ├── invoker_lambda.py
│   └── requirements.txt
├── ecs-worker/                     # Policy executor
│   ├── worker.py
│   ├── policy_selector.py
│   └── Dockerfile
├── policies/                       # Cloud Custodian policies (6 policies)
├── config/                         # Policy mappings
└── Documentation/
    ├── README.md                  # This file
    ├── DEPLOYMENT-GUIDE.md        # Step-by-step deployment
    ├── DEPLOYMENT-READY.md        # Quick start checklist
    └── GITHUB-SETUP.md            # GitHub Actions setup
```

## 📚 Resources

- [Cloud Custodian Documentation](https://cloudcustodian.io/docs/)
- [AWS EventBridge Patterns](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-event-patterns.html)
- [ECS Fargate Best Practices](https://docs.aws.amazon.com/AmazonECS/latest/bestpracticesguide/)

## 🤝 Contributing

1. Add new policies to `policies/` directory
2. Update `config/policy-mappings.yml` to map events
3. Test with GitHub Actions workflow
4. Submit PR with test results

## 📄 License

This project is part of the Cloud Custodian security automation framework.

---

**Next Steps:**
1. ✅ Deploy infrastructure
2. ✅ Test with S3 CreateBucket policy
3. ✅ Monitor execution logs
4. ✅ Add EC2 policy
5. ✅ Add IAM policy
6. ✅ Scale and optimize
