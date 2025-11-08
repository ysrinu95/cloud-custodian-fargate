# Package Lambda function with validators
# PowerShell script for Windows

Set-Location $PSScriptRoot

# Remove old package if exists
if (Test-Path "lambda-function.zip") {
    Remove-Item "lambda-function.zip"
    Write-Host "Removed old lambda-function.zip"
}

# Create zip package with invoker and validators
Compress-Archive -Path "invoker_lambda.py", "validators" -DestinationPath "lambda-function.zip" -Force

Write-Host "✓ Lambda deployment package created: lambda-function.zip" -ForegroundColor Green
Write-Host "  - invoker_lambda.py"
Write-Host "  - validators/__init__.py"
Write-Host "  - validators/base_validator.py"
Write-Host "  - validators/s3_validator.py"
Write-Host "  - validators/validator_factory.py"

Write-Host ""
Write-Host "Active Validators: S3 only" -ForegroundColor Cyan
Write-Host "Package created successfully!" -ForegroundColor Green
Write-Host "Deploy with: terraform apply" -ForegroundColor Yellow
