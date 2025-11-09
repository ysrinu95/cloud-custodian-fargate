# Monitor ECS Auto-Scaling Response Time
# This script tracks how long it takes for ECS to auto-scale based on SQS messages

$region = "us-east-1"
$cluster = "cloud-custodian-cluster"
$service = "cloud-custodian-worker-production"
$queueUrl = "https://sqs.us-east-1.amazonaws.com/172327596604/cloud-custodian-queue"

Write-Host "=== ECS Auto-Scaling Monitor ===" -ForegroundColor Cyan
Write-Host "Started at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Yellow
Write-Host ""

$startTime = Get-Date
$iteration = 0
$scaled = $false

while ($true) {
    $iteration++
    $currentTime = Get-Date
    $elapsed = [math]::Round(($currentTime - $startTime).TotalSeconds, 0)
    
    # Get SQS queue depth
    $queueAttrs = aws sqs get-queue-attributes --queue-url $queueUrl --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region $region | ConvertFrom-Json
    $messagesVisible = [int]$queueAttrs.Attributes.ApproximateNumberOfMessages
    $messagesInFlight = [int]$queueAttrs.Attributes.ApproximateNumberOfMessagesNotVisible
    $totalMessages = $messagesVisible + $messagesInFlight
    
    # Get ECS service status
    $serviceInfo = aws ecs describe-services --cluster $cluster --services $service --region $region --query 'services[0].[desiredCount,runningCount,pendingCount]' --output json | ConvertFrom-Json
    $desiredCount = [int]$serviceInfo[0]
    $runningCount = [int]$serviceInfo[1]
    $pendingCount = [int]$serviceInfo[2]
    
    # Display status
    Write-Host "[${elapsed}s] Iteration #${iteration}" -ForegroundColor Gray
    Write-Host "  Queue: $messagesVisible visible, $messagesInFlight in-flight (Total: $totalMessages)" -ForegroundColor White
    Write-Host "  ECS:   Desired=$desiredCount, Running=$runningCount, Pending=$pendingCount" -ForegroundColor White
    
    # Check if scaling occurred
    if ($desiredCount -gt 0 -and -not $scaled) {
        $scaled = $true
        Write-Host ""
        Write-Host "🎉 AUTO-SCALING TRIGGERED!" -ForegroundColor Green
        Write-Host "Time taken: ${elapsed} seconds ($([math]::Round($elapsed/60, 1)) minutes)" -ForegroundColor Green
        Write-Host "Desired count changed from 0 to $desiredCount" -ForegroundColor Green
        Write-Host ""
    }
    
    # Check if task is running
    if ($runningCount -gt 0) {
        Write-Host ""
        Write-Host "✅ TASK IS RUNNING!" -ForegroundColor Green
        Write-Host "Total time from start to running: ${elapsed} seconds ($([math]::Round($elapsed/60, 1)) minutes)" -ForegroundColor Green
        Write-Host ""
        
        # Continue monitoring for a bit to see task behavior
        if ($iteration -gt ($iteration + 3)) {
            break
        }
    }
    
    # Exit if queue is empty and no tasks
    if ($totalMessages -eq 0 -and $desiredCount -eq 0 -and $iteration -gt 5) {
        Write-Host ""
        Write-Host "Queue is empty and no scaling occurred after ${elapsed}s" -ForegroundColor Yellow
        break
    }
    
    # Wait before next check
    Start-Sleep -Seconds 30
}

Write-Host ""
Write-Host "Monitoring completed at: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Yellow
