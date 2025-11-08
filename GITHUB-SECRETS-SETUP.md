# GitHub Secrets Setup for Testing Workflow

## Required Secrets

To run the test workflow without Terraform, you need to set up these GitHub repository secrets:

### 1. AWS Authentication
```
AWS_ACCOUNT_ID
```
**Value:** Your AWS account ID (12 digits)
**Example:** `123456789012`

---

### 2. S3 Bucket Name
```
CUSTODIAN_BUCKET_NAME
```
**Value:** The S3 bucket created by Terraform for policies and outputs
**Example:** `aikyam-security-custodian-output` or `cloud-custodian-bucket-production`

**How to find it:**
```bash
# Option 1: From Terraform output
cd terraform
terraform output s3_bucket_name

# Option 2: From AWS CLI
aws s3 ls | grep custodian
```

---

### 3. Lambda Function Name (Optional but Recommended)
```
LAMBDA_FUNCTION_NAME
```
**Value:** The Lambda function name that processes events
**Example:** `cloud-custodian-invoker-production`

**How to find it:**
```bash
# Option 1: From Terraform output
cd terraform
terraform output lambda_function_name

# Option 2: From AWS CLI
aws lambda list-functions --query "Functions[?contains(FunctionName, 'custodian')].FunctionName"
```

---

### 4. ECS Cluster Name (Optional)
```
ECS_CLUSTER_NAME
```
**Value:** The ECS cluster name
**Example:** `cloud-custodian-cluster`

**How to find it:**
```bash
# Option 1: From Terraform
cd terraform
terraform output ecs_cluster_name

# Option 2: From AWS CLI
aws ecs list-clusters
```

---

### 5. SQS Queue Name (Optional)
```
SQS_QUEUE_NAME
```
**Value:** The SQS queue name
**Example:** `cloud-custodian-queue`

**How to find it:**
```bash
# Option 1: From Terraform
cd terraform
terraform output sqs_queue_name

# Option 2: From AWS CLI
aws sqs list-queues | grep custodian
```

---

## How to Add Secrets to GitHub

### Via GitHub Web UI:

1. Go to your repository: `https://github.com/ysrinu95/cloud-custodian-fargate`
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add each secret:
   - Name: `CUSTODIAN_BUCKET_NAME`
   - Value: `your-bucket-name`
   - Click **Add secret**
5. Repeat for all secrets

### Via GitHub CLI:

```bash
# Install GitHub CLI if needed
# https://cli.github.com/

# Authenticate
gh auth login

# Add secrets
gh secret set AWS_ACCOUNT_ID -b "123456789012"
gh secret set CUSTODIAN_BUCKET_NAME -b "your-bucket-name"
gh secret set LAMBDA_FUNCTION_NAME -b "cloud-custodian-invoker-production"
gh secret set ECS_CLUSTER_NAME -b "cloud-custodian-cluster"
gh secret set SQS_QUEUE_NAME -b "cloud-custodian-queue"
```

---

## Fallback Behavior

If secrets are not set, the workflow will:
1. Try to auto-detect resources by name pattern (e.g., buckets containing "custodian")
2. Show warnings if resources cannot be found
3. Continue with available information

**However, for reliable testing, it's recommended to set at least:**
- ✅ `AWS_ACCOUNT_ID` (required for OIDC authentication)
- ✅ `CUSTODIAN_BUCKET_NAME` (required for policy uploads)

---

## Verification

After adding secrets, verify they're set:

```bash
# List secrets (values are hidden)
gh secret list

# Expected output:
# AWS_ACCOUNT_ID          Updated 2024-11-08
# CUSTODIAN_BUCKET_NAME   Updated 2024-11-08
# LAMBDA_FUNCTION_NAME    Updated 2024-11-08
# ECS_CLUSTER_NAME        Updated 2024-11-08
# SQS_QUEUE_NAME          Updated 2024-11-08
```

---

## Testing After Setup

Once secrets are configured, test the workflow:

1. Go to **Actions** → **Test Policies One by One**
2. Click **Run workflow**
3. Select:
   - Policy: `s3-createbucket.yml`
   - Action: `validate-only` (safe test)
4. Click **Run workflow**

The workflow should now run without Terraform dependency errors!

---

## Quick Setup Script

Get all values at once with Terraform:

```bash
cd terraform

echo "Add these secrets to GitHub:"
echo ""
echo "AWS_ACCOUNT_ID: $(aws sts get-caller-identity --query Account --output text)"
echo "CUSTODIAN_BUCKET_NAME: $(terraform output -raw s3_bucket_name 2>/dev/null || echo 'not-found')"
echo "LAMBDA_FUNCTION_NAME: $(terraform output -raw lambda_function_name 2>/dev/null || echo 'not-found')"
echo "ECS_CLUSTER_NAME: $(terraform output -raw ecs_cluster_name 2>/dev/null || echo 'not-found')"
echo "SQS_QUEUE_NAME: $(terraform output -raw sqs_queue_name 2>/dev/null || echo 'not-found')"
```

Or use this one-liner to set them all:

```bash
cd terraform

gh secret set AWS_ACCOUNT_ID -b "$(aws sts get-caller-identity --query Account --output text)"
gh secret set CUSTODIAN_BUCKET_NAME -b "$(terraform output -raw s3_bucket_name)"
gh secret set LAMBDA_FUNCTION_NAME -b "$(terraform output -raw lambda_function_name)"
gh secret set ECS_CLUSTER_NAME -b "$(terraform output -raw ecs_cluster_name)"
gh secret set SQS_QUEUE_NAME -b "$(terraform output -raw sqs_queue_name)"

echo "✅ All secrets configured!"
```
