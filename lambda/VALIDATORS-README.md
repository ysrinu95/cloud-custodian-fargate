# Lambda Validators System

## Overview

The Lambda function now includes a **validation layer** that checks if security findings actually require remediation before queueing them to SQS. This prevents unnecessary container spin-ups and reduces costs.

## Architecture

```
EventBridge Event → Lambda
                      ↓
                Parse Finding
                      ↓
            **Validate Condition** ← Validators (S3/EC2/IAM)
                      ↓
              is_valid = True?
                    ↙    ↘
                 YES     NO
                  ↓       ↓
            Queue to SQS  Skip (log metric)
                  ↓
        ECS Auto-scales & Processes
```

## Validators

### Base Validator (`base_validator.py`)
Abstract base class defining the validator interface.

**Methods:**
- `get_resource_type()` - Returns resource type (e.g., 'S3', 'EC2')
- `validate(finding)` - Main validation logic
- `extract_resource_details(finding)` - Helper to extract common fields

### S3 Validator (`s3_validator.py`)
Validates if S3 buckets are actually public.

**Checks:**
- ✅ Public Access Block configuration (all 4 settings)
- ✅ Bucket ACL (AllUsers, AuthenticatedUsers grants)
- ✅ Bucket Policy (public statements)

**Example:**
```python
# Bucket with all public access blocks enabled → is_valid = False (skip)
# Bucket with public ACL → is_valid = True (remediate)
```

### EC2 Validator (`ec2_validator.py`)
Validates EC2 instance security group configurations.

**Checks:**
- ✅ Security groups with 0.0.0.0/0 on risky ports (22, 3389, 3306, etc.)
- ✅ Public IP assignment combined with open ports
- ✅ IMDSv1 enabled (metadata service v1)

**Risky Ports:**
SSH (22), RDP (3389), MySQL (3306), PostgreSQL (5432), MongoDB (27017), Redis (6379), Elasticsearch (9200), etc.

### IAM Validator (`iam_validator.py`)
Validates IAM resource security.

**Checks:**
- ✅ Users/Roles with AdministratorAccess policy
- ✅ Access keys without MFA
- ✅ Policies with wildcard resources (*)
- ✅ Policies with wildcard actions (*:*)
- ✅ Public assume role policies

### Validator Factory (`validator_factory.py`)
Dynamically selects the appropriate validator based on resource type.

**Methods:**
- `get_validator(resource_type)` - Returns validator instance
- `validate_finding(finding)` - Convenience method
- `register_validator(type, class)` - Add new validators at runtime

## Validation Flow

```python
# 1. Parse security finding
finding = parse_security_finding(event)

# 2. Validate finding
validation_result = ValidatorFactory.validate_finding(finding)

# 3. Check result
if validation_result['is_valid']:
    # Queue to SQS → ECS processes
    send_to_sqs(finding)
else:
    # Skip (log metric)
    publish_skipped_metric(finding)
```

## Response Format

All validators return standardized responses:

```python
{
    'is_valid': True,  # True = remediate, False = skip
    'reason': 'Bucket "my-bucket" has public access - remediation required',
    'metadata': {
        'bucket_name': 'my-bucket',
        'public_via_block_config': True,
        'public_via_acl': False,
        'public_via_policy': False
    },
    'validator': 'S3Validator'
}
```

## Fail-Open Strategy

All validators follow a **fail-open** approach:
- If validation API calls fail → `is_valid = True` (allow remediation)
- Rationale: Better to remediate a false positive than miss a real security issue
- All errors are logged for troubleshooting

## CloudWatch Metrics

**Published Metrics:**
- `FindingsReceived` - All findings received
- `FindingsQueued` - Findings queued to SQS (validation passed)
- `FindingsSkipped` - Findings skipped (validation failed)

**Dimensions:**
- Source (aws.securityhub, aws.guardduty, etc.)
- ResourceType (S3, EC2, IAM)
- Severity (CRITICAL, HIGH, MEDIUM, LOW)

## Adding New Validators

### Step 1: Create Validator Class

```python
# validators/rds_validator.py
from .base_validator import BaseValidator

class RDSValidator(BaseValidator):
    def get_resource_type(self) -> str:
        return 'RDS'
    
    def validate(self, finding: Dict[str, Any]) -> Dict[str, Any]:
        # Your validation logic here
        db_instance_id = finding.get('resource_id')
        
        # Check if RDS is publicly accessible
        is_public = self._check_public_access(db_instance_id)
        
        if is_public:
            return self.create_response(
                is_valid=True,
                reason=f"RDS instance {db_instance_id} is publicly accessible",
                metadata={'db_instance_id': db_instance_id}
            )
        else:
            return self.create_response(
                is_valid=False,
                reason=f"RDS instance {db_instance_id} is not public",
                metadata={'db_instance_id': db_instance_id}
            )
```

### Step 2: Register Validator

```python
# validators/validator_factory.py
from .rds_validator import RDSValidator

class ValidatorFactory:
    _validators = {
        'S3': S3Validator,
        'EC2': EC2Validator,
        'IAM': IAMValidator,
        'RDS': RDSValidator,  # Add new validator
    }
```

### Step 3: Add IAM Permissions

Update `terraform/unified-event-driven.tf`:

```hcl
{
  Effect = "Allow"
  Action = [
    "rds:DescribeDBInstances"
  ]
  Resource = "*"
}
```

### Step 4: Repackage and Deploy

```powershell
cd lambda
./package.ps1
cd ../terraform
terraform apply
```

## Testing

### Test S3 Validator
```python
finding = {
    'resource_type': 'S3',
    'resource_id': 'my-test-bucket',
    'region': 'us-east-1',
    'account': '123456789012'
}

result = ValidatorFactory.validate_finding(finding)
print(result)
```

### Test EC2 Validator
```python
finding = {
    'resource_type': 'EC2',
    'resource_id': 'i-1234567890abcdef0',
    'region': 'us-east-1',
    'account': '123456789012'
}

result = ValidatorFactory.validate_finding(finding)
print(result)
```

## Deployment

### Prerequisites
- Python 3.11
- Boto3 (included in Lambda runtime)

### Package Lambda Function

**Windows (PowerShell):**
```powershell
cd lambda
.\package.ps1
```

**Linux/Mac:**
```bash
cd lambda
chmod +x package.sh
./package.sh
```

### Deploy with Terraform

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

The Lambda package includes:
- `invoker_lambda.py` - Main handler
- `validators/__init__.py` - Package init
- `validators/base_validator.py` - Base class
- `validators/s3_validator.py` - S3 validation
- `validators/ec2_validator.py` - EC2 validation
- `validators/iam_validator.py` - IAM validation
- `validators/validator_factory.py` - Factory pattern

## Cost Optimization

**Before Validators:**
- Every S3 CreateBucket event → Lambda → SQS → ECS container spin-up
- **Cost:** $0.0004/GB-second * 30s * 512MB = ~$0.006 per event

**After Validators:**
- S3 CreateBucket for private bucket → Lambda validates → Skip SQS
- **Cost:** $0.0000002/request = $0.0000002 per event
- **Savings:** 30x cost reduction for false positives

**Example:**
- 1000 S3 bucket creations/month
- 900 are private (validation fails)
- 100 are public (validation passes)
- **Before:** 1000 container spin-ups = $6.00
- **After:** 100 container spin-ups = $0.60
- **Savings:** $5.40/month (90% reduction)

## Troubleshooting

### Validator Not Found
```
⚠ No validator registered for resource type: RDS
```
**Solution:** Add validator to `validator_factory.py` registry

### Permission Denied
```
Error validating S3 bucket: AccessDenied
```
**Solution:** Update Lambda IAM role in Terraform with required permissions

### Import Error
```
ModuleNotFoundError: No module named 'validators'
```
**Solution:** Ensure validators directory is included in zip package:
```powershell
cd lambda
.\package.ps1
```

### Validation Errors
Check Lambda logs:
```bash
aws logs tail /aws/lambda/cloud-custodian-invoker --follow
```

## Security Considerations

1. **Least Privilege:** Lambda IAM role only has read-only permissions for validation
2. **Fail-Open:** Errors don't block remediation (security over availability)
3. **Audit Trail:** All validation results logged to CloudWatch
4. **Encryption:** All API calls use HTTPS/TLS 1.2+

## Future Enhancements

- [ ] Add validators for Lambda, RDS, DynamoDB
- [ ] Support for custom validation rules via S3 config
- [ ] Parallel validation for multiple resources
- [ ] Validation result caching (TTL-based)
- [ ] Integration with AWS Config Rules
