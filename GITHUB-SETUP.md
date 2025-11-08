# GitHub Actions Setup Guide

## 🔐 Required GitHub Secrets

Configure these secrets in your GitHub repository:
**Settings → Secrets and variables → Actions → New repository secret**

### Essential Secrets

| Secret Name | Value | Description |
|------------|-------|-------------|
| `AWS_ACCOUNT_ID` | `172327596604` | Your AWS account ID |
| `AWS_REGION` | `us-east-1` | AWS region for deployment |
| `TERRAFORM_STATE_BUCKET` | `ysr95-cloud-custodian-tf-bkt` | S3 bucket for Terraform state |

### Optional Secrets (if not using OIDC)

| Secret Name | Value | Description |
|------------|-------|-------------|
| `AWS_ACCESS_KEY_ID` | Your access key | AWS credentials (if not using OIDC) |
| `AWS_SECRET_ACCESS_KEY` | Your secret key | AWS credentials (if not using OIDC) |

## 🔧 GitHub OIDC Setup (Recommended)

### 1. Verify OIDC Provider Exists

```bash
aws iam list-open-id-connect-providers
```

Look for: `arn:aws:iam::172327596604:oidc-provider/token.actions.githubusercontent.com`

### 2. Create GitHub Actions IAM Role

If not already created, run:

```bash
# Create trust policy
cat > github-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::172327596604:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:ysrinu95/cloud-custodian:*"
        }
      }
    }
  ]
}
EOF

# Create IAM role
aws iam create-role \
  --role-name GitHubActions-CloudCustodian-Role \
  --assume-role-policy-document file://github-trust-policy.json \
  --description "GitHub Actions role for Cloud Custodian deployment"

# Attach necessary policies
aws iam attach-role-policy \
  --role-name GitHubActions-CloudCustodian-Role \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess
```

### 3. Update GitHub Secrets

Add this additional secret:

| Secret Name | Value |
|------------|-------|
| `AWS_ROLE_ARN` | `arn:aws:iam::172327596604:role/GitHubActions-CloudCustodian-Role` |

## 🚀 Deployment Workflow

The workflow is triggered by:
- **Push to main** (automatic deployment)
- **Manual trigger** via `workflow_dispatch`

### Manual Deployment Steps

1. Go to **Actions** tab in GitHub
2. Select **Deploy Unified Event-Driven Infrastructure**
3. Click **Run workflow**
4. Choose action: `plan` → `apply` → `destroy`

### Automatic Deployment

Push changes to `main` branch:
```bash
git add .
git commit -m "Deploy unified infrastructure"
git push origin main
```

Files that trigger deployment:
- `terraform/unified-event-driven.tf`
- `lambda/**`
- `ecs-worker/**`
- `config/**`
- `policies/**`
- `.github/workflows/deploy-unified-infrastructure.yml`

## 📊 Monitoring Deployments

### View Workflow Runs
1. Go to **Actions** tab
2. Click on workflow run
3. View logs for each step

### Check Terraform Plan
Before applying, review the plan output in the workflow logs.

### Verify Deployment
After successful deployment, check:
- ✅ Lambda function created
- ✅ SQS queues created
- ✅ ECS cluster and service running
- ✅ EventBridge rules active
- ✅ S3 bucket with policies uploaded

## 🔍 Troubleshooting

### Authentication Failed
- Verify GitHub secrets are set correctly
- Check IAM role trust policy includes your repository
- Ensure OIDC provider exists

### Terraform State Lock
If deployment fails with state lock error:
```bash
aws dynamodb delete-item \
  --table-name terraform-state-lock \
  --key '{"LockID": {"S": "ysr95-cloud-custodian-tf-bkt/terraform/unified-event-driven/terraform.tfstate"}}'
```

### ECR Push Failed
- Verify ECR repository exists
- Check Docker is available in workflow
- Ensure IAM role has ECR permissions

## 🧪 Testing Workflow

Use the test workflow to validate policies:

1. Go to **Actions** tab
2. Select **Test Cloud Custodian Policies**
3. Click **Run workflow**
4. Choose:
   - **Policy file**: e.g., `s3-createbucket-simple.yml`
   - **Test mode**: `validate`, `dry-run`, or `deploy-test`

## ⚠️ Important Notes

- **Never commit secrets** to the repository
- **Review Terraform plans** before applying
- **Test in non-prod** before production deployment
- **Monitor costs** - ECS Fargate incurs charges
- **Use workflow_dispatch** for controlled deployments

## 📞 Support

If you encounter issues:
1. Check workflow logs in GitHub Actions
2. Review CloudWatch logs in AWS
3. Verify all secrets are configured correctly
4. Ensure AWS credentials have necessary permissions

---

**Last Updated**: November 8, 2025
**Repository**: ysrinu95/cloud-custodian
**AWS Account**: 172327596604
