# Package ECS Scaler Lambda function
Write-Host "Packaging ECS Scaler Lambda function..." -ForegroundColor Cyan

Set-Location lambda

# Remove old package if exists
if (Test-Path "ecs-scaler-function.zip") {
    Remove-Item "ecs-scaler-function.zip" -Force
}

# Create zip with just the scaler file (no dependencies needed)
Compress-Archive -Path "ecs_scaler.py" -DestinationPath "ecs-scaler-function.zip" -Force

Write-Host "Package created: lambda/ecs-scaler-function.zip" -ForegroundColor Green
Get-Item "ecs-scaler-function.zip"
