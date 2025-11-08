#!/bin/bash
# Package Lambda function with validators
# This script creates a deployment package including all validators

cd "$(dirname "$0")"

# Remove old package if exists
rm -f lambda-function.zip

# Create zip package with invoker and validators
zip -r lambda-function.zip invoker_lambda.py validators/

echo "✓ Lambda deployment package created: lambda-function.zip"
echo "  - invoker_lambda.py"
echo "  - validators/__init__.py"
echo "  - validators/base_validator.py"
echo "  - validators/s3_validator.py"
echo "  - validators/validator_factory.py"
echo ""
echo "Active Validators: S3 only"

# Show package contents
echo ""
echo "Package contents:"
unzip -l lambda-function.zip
