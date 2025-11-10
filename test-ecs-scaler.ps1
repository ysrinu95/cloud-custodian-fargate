# Monitor ECS Scaler Lambda Deployment and Test
Write-Host "=== ECS Scaler Deployment Monitor ===" -ForegroundColor Cyan
Write-Host ""

$region = "us-east-1"
$cluster = "cloud-custodian-cluster"
$service = "cloud-custodian-worker-production"
$scalerFunction = "cloud-custodian-ecs-scaler-production"

# Wait for Lambda to be deployed
Write-Host "Checking if ECS Scaler Lambda is deployed..." -ForegroundColor Yellow

$deployed = $false
$attempts = 0
$maxAttempts = 30  # 5 minutes (30 * 10 seconds)

while (-not $deployed -and $attempts -lt $maxAttempts) {
    $attempts++
    
    try {
        $lambda = aws lambda get-function --function-name $scalerFunction --region $region 2>$null | ConvertFrom-Json
        if ($lambda) {
            $deployed = $true
            Write-Host "✓ ECS Scaler Lambda is deployed!" -ForegroundColor Green
            Write-Host "  Function: $($lambda.Configuration.FunctionName)" -ForegroundColor Gray
            Write-Host "  Runtime: $($lambda.Configuration.Runtime)" -ForegroundColor Gray
            Write-Host "  Handler: $($lambda.Configuration.Handler)" -ForegroundColor Gray
            Write-Host ""
            break
        }
    }
    catch {
        # Lambda not deployed yet
    }
    
    Write-Host "  Attempt $attempts/$maxAttempts - Waiting for deployment..." -ForegroundColor Gray
    Start-Sleep -Seconds 10
}

if (-not $deployed) {
    Write-Host "✗ ECS Scaler Lambda not deployed after $maxAttempts attempts" -ForegroundColor Red
    Write-Host "Please check GitHub Actions workflow: https://github.com/ysrinu95/cloud-custodian-fargate/actions" -ForegroundColor Yellow
    exit 1
}

# Check event source mapping
Write-Host "Checking SQS event source mapping..." -ForegroundColor Yellow
$mappings = aws lambda list-event-source-mappings --function-name $scalerFunction --region $region | ConvertFrom-Json

if ($mappings.EventSourceMappings.Count -gt 0) {
    $mapping = $mappings.EventSourceMappings[0]
    Write-Host "✓ Event source mapping configured" -ForegroundColor Green
    Write-Host "  State: $($mapping.State)" -ForegroundColor Gray
    Write-Host "  Batch Size: $($mapping.BatchSize)" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "✗ No event source mapping found" -ForegroundColor Red
}

# Monitor ECS scaling
Write-Host "Now monitoring for ECS scaling..." -ForegroundColor Cyan
Write-Host "When you create a public S3 bucket, the ECS scaler should:" -ForegroundColor White
Write-Host "  1. Receive SQS message trigger" -ForegroundColor White
Write-Host "  2. Scale ECS service from 0 to 1 task" -ForegroundColor White
Write-Host "  3. Task starts in 10-20 seconds" -ForegroundColor White
Write-Host ""

$startTime = Get-Date
$iteration = 0

while ($true) {
    $iteration++
    $elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 0)
    
    # Get current ECS status
    $serviceInfo = aws ecs describe-services --cluster $cluster --services $service --region $region --query 'services[0].[desiredCount,runningCount,pendingCount]' --output json | ConvertFrom-Json
    $desiredCount = [int]$serviceInfo[0]
    $runningCount = [int]$serviceInfo[1]
    $pendingCount = [int]$serviceInfo[2]
    
    Write-Host "[$elapsed`s] ECS Status: Desired=$desiredCount, Running=$runningCount, Pending=$pendingCount" -ForegroundColor Gray
    
    if ($desiredCount -gt 0) {
        Write-Host ""
        Write-Host "🎉 ECS SCALED UP! Desired count changed to $desiredCount" -ForegroundColor Green
        Write-Host "Time: $elapsed seconds" -ForegroundColor Green
        
        # Check Lambda logs
        Write-Host ""
        Write-Host "Recent ECS Scaler Lambda logs:" -ForegroundColor Cyan
        aws logs tail "/aws/lambda/$scalerFunction" --since 5m --format short --region $region
        break
    }
    
    if ($iteration -eq 1) {
        Write-Host "Waiting for S3 bucket creation event..." -ForegroundColor Yellow
    }
    
    Start-Sleep -Seconds 10
}

Write-Host ""
Write-Host "Monitoring complete!" -ForegroundColor Cyan
