"""
Fargate Worker Application
Long-running process that polls SQS and executes Cloud Custodian policy
"""

import json
import boto3
import os
import time
import sys
from datetime import datetime
from typing import Dict, Any, Optional
import traceback

# AWS clients
sqs = boto3.client('sqs')
s3 = boto3.client('s3')
cloudwatch = boto3.client('cloudwatch')

# Environment variables
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
OUTPUT_BUCKET = os.environ.get('OUTPUT_BUCKET', '')
NOTIFICATION_QUEUE_URL = os.environ.get('NOTIFICATION_QUEUE_URL', '')
MAX_MESSAGES = int(os.environ.get('MAX_MESSAGES', '10'))
WAIT_TIME_SECONDS = int(os.environ.get('WAIT_TIME_SECONDS', '20'))
VISIBILITY_TIMEOUT = int(os.environ.get('VISIBILITY_TIMEOUT', '3600'))
POLICY_BUCKET = os.environ.get('POLICY_BUCKET', 'aikyam-security-custodian-output')
POLICY_KEY = os.environ.get('POLICY_KEY', 'policies/unified-security-policy.yml')


def main():
    """
    Main worker loop - continuously poll SQS and process findings
    """
    print(f"Starting Fargate worker at {datetime.utcnow().isoformat()}")
    print(f"SQS Queue: {SQS_QUEUE_URL}")
    print(f"Output Bucket: {OUTPUT_BUCKET}")
    
    # Health check
    if not SQS_QUEUE_URL:
        print("ERROR: SQS_QUEUE_URL environment variable not set")
        sys.exit(1)
    
    consecutive_empty_receives = 0
    max_empty_receives = 10
    
    while True:
        try:
            print(f"\n[{datetime.utcnow().isoformat()}] Polling SQS queue...")
            
            # Receive messages from SQS
            response = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=WAIT_TIME_SECONDS,  # Long polling
                VisibilityTimeout=VISIBILITY_TIMEOUT,
                MessageAttributeNames=['All'],
                AttributeNames=['All']
            )
            
            messages = response.get('Messages', [])
            
            if not messages:
                consecutive_empty_receives += 1
                print(f"No messages received (empty count: {consecutive_empty_receives}/{max_empty_receives})")
                
                # Graceful shutdown after prolonged inactivity
                if consecutive_empty_receives >= max_empty_receives:
                    print("No messages for extended period. Exiting gracefully for scale-down.")
                    publish_worker_metrics(0, 0, 0)
                    break
                
                continue
            
            # Reset empty receive counter
            consecutive_empty_receives = 0
            
            print(f"Received {len(messages)} message(s)")
            
            # Process messages
            successes = 0
            failures = 0
            
            for message in messages:
                try:
                    result = process_message(message)
                    
                    if result['success']:
                        # Delete message from queue
                        sqs.delete_message(
                            QueueUrl=SQS_QUEUE_URL,
                            ReceiptHandle=message['ReceiptHandle']
                        )
                        successes += 1
                        print(f"✓ Successfully processed finding {result.get('finding_id', 'unknown')}")
                    else:
                        failures += 1
                        print(f"✗ Failed to process finding: {result.get('error', 'unknown')}")
                        
                except Exception as e:
                    failures += 1
                    print(f"✗ Error processing message: {str(e)}")
                    traceback.print_exc()
            
            # Publish metrics
            publish_worker_metrics(len(messages), successes, failures)
            
            print(f"Batch complete: {successes} successes, {failures} failures")
            
        except KeyboardInterrupt:
            print("\nReceived interrupt signal. Shutting down gracefully...")
            break
            
        except Exception as e:
            print(f"Error in main loop: {str(e)}")
            traceback.print_exc()
            time.sleep(5)  # Brief pause before retrying


def process_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single SQS message containing a security finding
    Executes a single unified Cloud Custodian policy
    """
    start_time = time.time()
    
    try:
        # Parse message body
        body = json.loads(message['Body'])
        
        finding_id = body.get('finding_id', 'unknown')
        source = body.get('source', '')
        resource_type = body.get('resource_type', '')
        finding_type = body.get('finding_type', '')
        severity = body.get('severity', 'MEDIUM')
        
        print(f"\nProcessing finding:")
        print(f"  ID: {finding_id}")
        print(f"  Source: {source}")
        print(f"  Resource Type: {resource_type}")
        print(f"  Finding Type: {finding_type}")
        print(f"  Severity: {severity}")
        
        # Get policy configuration from Lambda (or fallback to environment variables)
        policy_config = body.get('policy_config', {})
        policy_bucket = policy_config.get('policy_bucket', POLICY_BUCKET)
        policy_key = policy_config.get('policy_key', POLICY_KEY)
        
        print(f"  Policy location: s3://{policy_bucket}/{policy_key}")
        
        # Download the policy file from S3
        local_policy_dir = '/tmp/policies'
        local_policy_path = os.path.join(local_policy_dir, 'policy.yml')
        
        try:
            os.makedirs(local_policy_dir, exist_ok=True)
            print(f"  Downloading policy from S3: s3://{policy_bucket}/{policy_key}")
            s3.download_file(policy_bucket, policy_key, local_policy_path)
            print(f"  ✓ Policy downloaded: {local_policy_path}")
        except Exception as e:
            print(f"  ✗ Failed to download policy from S3: {e}")
            send_notification(body, "POLICY_DOWNLOAD_FAILED", {'error': str(e)})
            return {
                'success': False,
                'finding_id': finding_id,
                'error': f'Policy download failed: {e}'
            }
        
        # Execute Cloud Custodian policy
        result = execute_custodian_policy(
            policy_file=local_policy_path,
            finding=body
        )
        
        # Calculate execution time
        execution_time = time.time() - start_time
        
        if result['success']:
            print(f"  ✓ Policy executed successfully ({execution_time:.2f}s)")
            print(f"  Resources processed: {result.get('resources_processed', 0)}")
            print(f"  Actions taken: {result.get('actions_taken', 0)}")
            
            # Send success notification for CRITICAL/HIGH findings
            if severity in ['CRITICAL', 'HIGH']:
                send_notification(body, "REMEDIATED", result)
            
            # Publish detailed metrics
            publish_execution_metrics(
                execution_time=execution_time,
                resources_processed=result.get('resources_processed', 0),
                actions_taken=result.get('actions_taken', 0),
                resource_type=resource_type
            )
            
            return {
                'success': True,
                'finding_id': finding_id,
                'execution_time': execution_time,
                **result
            }
        else:
            print(f"  ✗ Policy execution failed: {result.get('error', 'unknown')}")
            
            # Send failure notification
            send_notification(body, "EXECUTION_FAILED", result)
            
            return {
                'success': False,
                'finding_id': finding_id,
                'error': result.get('error', 'unknown')
            }
        
    except Exception as e:
        print(f"Error processing message: {str(e)}")
        traceback.print_exc()
        
        return {
            'success': False,
            'error': str(e)
        }


def execute_custodian_policy(policy_file: str, finding: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute Cloud Custodian policy using subprocess
    """
    import subprocess
    import tempfile
    
    try:
        # Prepare output directory
        output_dir = tempfile.mkdtemp(prefix='c7n-output-')
        
        # Build custodian run command
        cmd = [
            'custodian', 'run',
            '-s', output_dir,
            '--region', finding.get('region', os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')),
            policy_file
        ]
        
        # Set environment variables for policy execution
        env = os.environ.copy()
        resource_id = finding.get('resource_id', '')
        
        env.update({
            'FINDING_ID': finding.get('finding_id', ''),
            'RESOURCE_ID': resource_id,
            'RESOURCE_IDS': resource_id,  # For comma-separated IDs
            'SEVERITY': finding.get('severity', 'MEDIUM'),
            'SOURCE': finding.get('source', ''),
        })
        
        if resource_id:
            print(f"  Targeting specific resources via env var RESOURCE_ID: {resource_id}")
        
        # Set environment variables for policy execution
        env = os.environ.copy()
        env.update({
            'FINDING_ID': finding.get('finding_id', ''),
            'RESOURCE_ID': resource_id,
            'SEVERITY': finding.get('severity', 'MEDIUM'),
            'SOURCE': finding.get('source', ''),
        })
        
        print(f"  Executing: {' '.join(cmd)}")
        
        # Execute policy
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout per policy
        )
        
        if result.returncode == 0:
            # Parse output for statistics
            output = result.stdout
            
            # Extract resource counts from output
            resources_processed = 0
            actions_taken = 0
            
            # Parse c7n output (format: "policy_name: X resources matched, Y actions taken")
            for line in output.split('\n'):
                if 'resources' in line.lower():
                    try:
                        resources_processed = int(line.split()[0])
                    except:
                        pass
                if 'actions' in line.lower() or 'action' in line.lower():
                    try:
                        actions_taken = int(line.split()[0])
                    except:
                        pass
            
            # Upload output to S3 if configured
            if OUTPUT_BUCKET:
                upload_output_to_s3(output_dir, finding)
            
            return {
                'success': True,
                'resources_processed': resources_processed,
                'actions_taken': actions_taken,
                'output': output,
                'output_dir': output_dir
            }
        else:
            return {
                'success': False,
                'error': result.stderr,
                'returncode': result.returncode
            }
        
    except subprocess.TimeoutExpired:
        return {
            'success': False,
            'error': 'Policy execution timeout (>5 minutes)'
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def upload_output_to_s3(output_dir: str, finding: Dict[str, Any]):
    """
    Upload Cloud Custodian output to S3
    """
    try:
        s3 = boto3.client('s3')
        
        # Generate S3 key prefix
        timestamp = datetime.utcnow().strftime('%Y/%m/%d/%H')
        finding_id = finding.get('finding_id', 'unknown').replace(':', '-')
        prefix = f"custodian-output/{timestamp}/{finding_id}/"
        
        # Upload all files in output directory
        import os
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, output_dir)
                s3_key = prefix + relative_path
                
                s3.upload_file(local_path, OUTPUT_BUCKET, s3_key)
                print(f"  Uploaded: s3://{OUTPUT_BUCKET}/{s3_key}")
        
    except Exception as e:
        print(f"  Warning: Failed to upload output to S3: {str(e)}")


def send_notification(finding: Dict[str, Any], status: str, result: Optional[Dict[str, Any]] = None):
    """
    Send notification about finding processing
    """
    if not NOTIFICATION_QUEUE_URL:
        return
    
    try:
        notification = {
            'timestamp': datetime.utcnow().isoformat(),
            'status': status,
            'finding': finding,
            'result': result
        }
        
        sqs.send_message(
            QueueUrl=NOTIFICATION_QUEUE_URL,
            MessageBody=json.dumps(notification)
        )
        
    except Exception as e:
        print(f"  Warning: Failed to send notification: {str(e)}")


def publish_worker_metrics(messages_received: int, successes: int, failures: int):
    """
    Publish worker-level CloudWatch metrics
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='CloudCustodian/FargateWorker',
            MetricData=[
                {
                    'MetricName': 'MessagesReceived',
                    'Value': messages_received,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'ProcessingSuccesses',
                    'Value': successes,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'ProcessingFailures',
                    'Value': failures,
                    'Unit': 'Count'
                }
            ]
        )
    except Exception as e:
        print(f"Warning: Failed to publish worker metrics: {str(e)}")


def publish_execution_metrics(execution_time: float, resources_processed: int, 
                              actions_taken: int, resource_type: str):
    """
    Publish policy execution CloudWatch metrics
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='CloudCustodian/PolicyExecution',
            MetricData=[
                {
                    'MetricName': 'ExecutionTime',
                    'Value': execution_time,
                    'Unit': 'Seconds',
                    'Dimensions': [
                        {'Name': 'ResourceType', 'Value': resource_type}
                    ]
                },
                {
                    'MetricName': 'ResourcesProcessed',
                    'Value': resources_processed,
                    'Unit': 'Count',
                    'Dimensions': [
                        {'Name': 'ResourceType', 'Value': resource_type}
                    ]
                },
                {
                    'MetricName': 'ActionsTaken',
                    'Value': actions_taken,
                    'Unit': 'Count',
                    'Dimensions': [
                        {'Name': 'ResourceType', 'Value': resource_type}
                    ]
                }
            ]
        )
    except Exception as e:
        print(f"Warning: Failed to publish execution metrics: {str(e)}")


if __name__ == '__main__':
    main()
