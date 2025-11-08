"""
Lightweight Invoker Lambda
Receives security findings from EventBridge and dynamically selects appropriate policy
"""

import json
import boto3
import os
from datetime import datetime
from typing import Dict, Any, Optional

sqs = boto3.client('sqs')
s3 = boto3.client('s3')
cloudwatch = boto3.client('cloudwatch')

# Environment variables
SQS_QUEUE_URL = os.environ.get('SQS_QUEUE_URL')
ENABLE_ENRICHMENT = os.environ.get('ENABLE_ENRICHMENT', 'true').lower() == 'true'
POLICY_BUCKET = os.environ.get('POLICY_BUCKET', '')
POLICY_MAPPING_KEY = os.environ.get('POLICY_MAPPING_KEY', 'config/policy-mappings.json')
DEFAULT_POLICY_KEY = os.environ.get('DEFAULT_POLICY_KEY', 'policies/s3-createbucket.yml')

# Cache for policy mappings (loaded once per Lambda container lifecycle)
_policy_mappings_cache = None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Handler that parses security findings and dynamically selects appropriate policy
    """
    print(f"Received event: {json.dumps(event)}")
    
    try:
        # Extract finding metadata
        finding = parse_security_finding(event)
        
        if not finding:
            print("Could not parse security finding")
            return {'statusCode': 400, 'body': 'Invalid finding format'}
        
        # Enrich finding with additional context (optional)
        if ENABLE_ENRICHMENT:
            finding = enrich_finding(finding)
        
        # Dynamically select appropriate policy based on finding characteristics
        policy_key = select_policy_for_finding(finding)
        
        if not policy_key:
            print(f"⚠ No policy matched for finding {finding.get('finding_id')}, using default")
            policy_key = DEFAULT_POLICY_KEY
        
        print(f"✓ Selected policy: {policy_key}")
        
        # Add policy configuration to the finding
        finding['policy_config'] = {
            'policy_bucket': POLICY_BUCKET,
            'policy_key': policy_key
        }
        
        # Determine processing priority
        priority = get_priority(finding)
        
        # Send to SQS
        message_id = send_to_sqs(finding, priority)
        
        # Publish metrics
        publish_metrics(finding, policy_key)
        
        print(f"Successfully queued finding {finding.get('finding_id')} with message ID {message_id}")
        print(f"Policy configuration: s3://{POLICY_BUCKET}/{policy_key}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message_id': message_id,
                'finding_id': finding.get('finding_id'),
                'policy_bucket': POLICY_BUCKET,
                'policy_key': policy_key,
                'priority': priority
            })
        }
        
    except Exception as e:
        print(f"Error processing finding: {str(e)}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


def parse_security_finding(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse security finding from various AWS security services
    Returns normalized finding structure
    """
    source = event.get('source', '')
    detail = event.get('detail', {})
    
    # Common structure for all findings
    finding = {
        'source': source,
        'detail_type': event.get('detail-type', ''),
        'account': event.get('account', ''),
        'region': event.get('region', ''),
        'time': event.get('time', ''),
        'raw_event': event  # Keep original for reference
    }
    
    # Parse based on source
    if source == 'aws.securityhub':
        findings_list = detail.get('findings', [])
        if findings_list:
            sh_finding = findings_list[0]
            finding.update({
                'finding_id': sh_finding.get('Id', ''),
                'finding_type': sh_finding.get('Types', [''])[0],
                'severity': sh_finding.get('Severity', {}).get('Label', 'MEDIUM'),
                'title': sh_finding.get('Title', ''),
                'description': sh_finding.get('Description', ''),
                'resource_type': extract_resource_type_securityhub(sh_finding),
                'resource_id': extract_resource_id_securityhub(sh_finding),
                'resource_arn': extract_resource_arn_securityhub(sh_finding),
                'created_at': sh_finding.get('CreatedAt', ''),
            })
    
    elif source == 'aws.guardduty':
        finding.update({
            'finding_id': detail.get('id', ''),
            'finding_type': detail.get('type', ''),
            'severity': map_guardduty_severity(detail.get('severity', 5.0)),
            'title': detail.get('title', ''),
            'description': detail.get('description', ''),
            'resource_type': detail.get('resource', {}).get('resourceType', ''),
            'resource_id': extract_resource_id_guardduty(detail),
            'created_at': detail.get('createdAt', ''),
        })
    
    elif source == 'aws.config':
        finding.update({
            'finding_id': detail.get('configRuleInvocationEvent', {}).get('configRuleId', ''),
            'finding_type': detail.get('configRuleName', ''),
            'severity': 'HIGH' if detail.get('newEvaluationResult', {}).get('complianceType') == 'NON_COMPLIANT' else 'LOW',
            'title': f"Config Rule: {detail.get('configRuleName', '')}",
            'description': detail.get('newEvaluationResult', {}).get('annotation', ''),
            'resource_type': extract_resource_type_config(detail.get('resourceType', '')),
            'resource_id': detail.get('resourceId', ''),
            'created_at': detail.get('notificationCreationTime', ''),
        })
    
    elif source == 'aws.macie':
        finding.update({
            'finding_id': detail.get('id', ''),
            'finding_type': detail.get('classificationDetails', {}).get('result', {}).get('sensitiveData', [{}])[0].get('category', ''),
            'severity': detail.get('severity', {}).get('description', 'MEDIUM'),
            'title': detail.get('title', ''),
            'description': detail.get('description', ''),
            'resource_type': 'S3',
            'resource_id': detail.get('resourcesAffected', {}).get('s3Bucket', {}).get('name', ''),
            'created_at': detail.get('createdAt', ''),
        })
    
    elif source in ['aws.cloudtrail', 'aws.ec2', 'aws.s3', 'aws.iam']:
        event_name = detail.get('eventName', '')
        resource_type = extract_resource_type_cloudtrail(detail)
        resource_id = extract_resource_id_cloudtrail(detail)
        
        print(f"CloudTrail event from {source}: {event_name}, resource_type: {resource_type}, resource_id: {resource_id}")
        
        finding.update({
            'finding_id': detail.get('eventID', event_name + '-' + str(hash(str(detail)))),
            'finding_type': event_name,
            'severity': 'HIGH',
            'title': f"High-risk API call: {event_name}",
            'description': f"{detail.get('eventSource', '')} - {event_name}",
            'resource_type': resource_type,
            'resource_id': resource_id,
            'created_at': detail.get('eventTime', ''),
        })
    
    # Validate required fields
    if finding.get('resource_type') and finding.get('finding_id'):
        return finding
    
    print(f"Validation failed - resource_type: {finding.get('resource_type')}, finding_id: {finding.get('finding_id')}")
    return None


def extract_resource_type_securityhub(finding: Dict[str, Any]) -> str:
    """Extract resource type from Security Hub finding"""
    resources = finding.get('Resources', [])
    if resources:
        resource_type = resources[0].get('Type', '')
        # Extract service name (e.g., AwsEc2Instance -> EC2)
        if '::' in resource_type:
            return resource_type.split('::')[1]
        elif resource_type.startswith('Aws'):
            return resource_type[3:].split('::')[0]
    return ''


def extract_resource_id_securityhub(finding: Dict[str, Any]) -> str:
    """Extract resource ID from Security Hub finding"""
    resources = finding.get('Resources', [])
    if resources:
        resource_id = resources[0].get('Id', '')
        # Extract ID from ARN if needed
        if resource_id.startswith('arn:'):
            parts = resource_id.split('/')
            return parts[-1] if parts else resource_id
        return resource_id
    return ''


def extract_resource_arn_securityhub(finding: Dict[str, Any]) -> str:
    """Extract resource ARN from Security Hub finding"""
    resources = finding.get('Resources', [])
    if resources:
        return resources[0].get('Id', '')
    return ''


def extract_resource_id_guardduty(detail: Dict[str, Any]) -> str:
    """Extract resource ID from GuardDuty finding"""
    resource = detail.get('resource', {})
    
    # Try instance details
    instance_details = resource.get('instanceDetails', {})
    if instance_details:
        return instance_details.get('instanceId', '')
    
    # Try access key details
    access_key_details = resource.get('accessKeyDetails', {})
    if access_key_details:
        return access_key_details.get('accessKeyId', '')
    
    # Try S3 bucket details
    s3_bucket_details = resource.get('s3BucketDetails', [])
    if s3_bucket_details:
        return s3_bucket_details[0].get('name', '')
    
    return ''


def extract_resource_type_config(resource_type: str) -> str:
    """Extract resource type from Config resource type"""
    if '::' in resource_type:
        parts = resource_type.split('::')
        return parts[1] if len(parts) > 1 else ''
    return resource_type


def extract_resource_type_cloudtrail(detail: Dict[str, Any]) -> str:
    """Extract resource type from CloudTrail event"""
    event_source = detail.get('eventSource', '').split('.')[0]
    resource_map = {
        'iam': 'IAM',
        'ec2': 'EC2',
        's3': 'S3',
        'rds': 'RDS',
        'lambda': 'Lambda',
    }
    return resource_map.get(event_source, 'Unknown')


def extract_resource_id_cloudtrail(detail: Dict[str, Any]) -> str:
    """Extract resource ID from CloudTrail event"""
    event_name = detail.get('eventName', '')
    
    # For EC2 RunInstances, extract instance IDs from response
    if event_name == 'RunInstances':
        response_elements = detail.get('responseElements', {})
        instances_set = response_elements.get('instancesSet', {}).get('items', [])
        if instances_set:
            # Return comma-separated instance IDs if multiple instances created
            instance_ids = [item.get('instanceId', '') for item in instances_set if item.get('instanceId')]
            return ','.join(instance_ids) if instance_ids else ''
    
    # For S3 events (CreateBucket, PutBucketPolicy, PutBucketAcl, etc.)
    if event_name in ['CreateBucket', 'PutBucketPolicy', 'PutBucketAcl', 'DeleteBucketPolicy', 
                       'PutBucketPublicAccessBlock', 'DeleteBucketPublicAccessBlock']:
        request_params = detail.get('requestParameters', {})
        bucket_name = request_params.get('bucketName', '')
        if bucket_name:
            return bucket_name
        # Also try resources list for bucket ARN
        resources = detail.get('resources', [])
        if resources:
            for resource in resources:
                arn = resource.get('ARN', '')
                if 'arn:aws:s3:::' in arn:
                    # Extract bucket name from ARN: arn:aws:s3:::bucket-name
                    bucket_name = arn.replace('arn:aws:s3:::', '').split('/')[0]
                    if bucket_name:
                        return bucket_name
    
    # Try resources list
    resources = detail.get('resources', [])
    if resources:
        arn = resources[0].get('ARN', '')
        return arn.split('/')[-1] if '/' in arn else arn
    
    # Try request parameters
    request_params = detail.get('requestParameters', {})
    return request_params.get('instanceId', request_params.get('bucketName', ''))


def map_guardduty_severity(severity_score: float) -> str:
    """Map GuardDuty severity score to standard labels"""
    if severity_score >= 7.0:
        return 'CRITICAL'
    elif severity_score >= 4.0:
        return 'HIGH'
    elif severity_score >= 1.0:
        return 'MEDIUM'
    else:
        return 'LOW'


def enrich_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enrich finding with additional context
    This is optional and can be disabled via environment variable
    """
    finding['enrichment'] = {
        'invoker_timestamp': datetime.utcnow().isoformat(),
        'lambda_request_id': os.environ.get('AWS_REQUEST_ID', ''),
    }
    
    # Could add more enrichment here:
    # - Resource tags (via AWS API calls)
    # - Account/region metadata
    # - Historical compliance data
    # - Owner information from CMDB
    
    return finding


def get_priority(finding: Dict[str, Any]) -> str:
    """
    Determine message priority based on severity
    Higher priority findings are processed first
    """
    severity = finding.get('severity', 'MEDIUM')
    
    priority_map = {
        'CRITICAL': '1',
        'HIGH': '2',
        'MEDIUM': '3',
        'LOW': '4',
        'INFORMATIONAL': '5'
    }
    
    return priority_map.get(severity, '3')


def send_to_sqs(finding: Dict[str, Any], priority: str) -> str:
    """
    Send finding to SQS queue for Fargate processing
    """
    message_body = json.dumps(finding)
    
    response = sqs.send_message(
        QueueUrl=SQS_QUEUE_URL,
        MessageBody=message_body,
        MessageAttributes={
            'Priority': {
                'StringValue': priority,
                'DataType': 'String'
            },
            'Severity': {
                'StringValue': finding.get('severity', 'MEDIUM'),
                'DataType': 'String'
            },
            'Source': {
                'StringValue': finding.get('source', ''),
                'DataType': 'String'
            },
            'ResourceType': {
                'StringValue': finding.get('resource_type', ''),
                'DataType': 'String'
            }
        }
    )
    
    return response.get('MessageId', '')


def publish_metrics(finding: Dict[str, Any], policy_key: str) -> None:
    """
    Publish CloudWatch metrics for monitoring
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='CloudCustodian/SecurityFindings',
            MetricData=[
                {
                    'MetricName': 'FindingsReceived',
                    'Value': 1,
                    'Unit': 'Count',
                    'Dimensions': [
                        {'Name': 'Source', 'Value': finding.get('source', 'unknown')},
                        {'Name': 'Severity', 'Value': finding.get('severity', 'unknown')},
                        {'Name': 'ResourceType', 'Value': finding.get('resource_type', 'unknown')},
                    ]
                },
                {
                    'MetricName': 'FindingsQueued',
                    'Value': 1,
                    'Unit': 'Count',
                    'Dimensions': [
                        {'Name': 'Source', 'Value': finding.get('source', 'unknown')},
                    ]
                },
                {
                    'MetricName': 'PolicySelected',
                    'Value': 1,
                    'Unit': 'Count',
                    'Dimensions': [
                        {'Name': 'PolicyKey', 'Value': policy_key},
                        {'Name': 'ResourceType', 'Value': finding.get('resource_type', 'unknown')},
                    ]
                }
            ]
        )
    except Exception as e:
        print(f"Error publishing metrics: {str(e)}")
        # Don't fail the Lambda on metrics errors


def load_policy_mappings() -> Dict[str, Any]:
    """
    Load policy mappings from S3 and cache them
    Uses Lambda container reuse for efficiency
    """
    global _policy_mappings_cache
    
    if _policy_mappings_cache is not None:
        return _policy_mappings_cache
    
    try:
        print(f"Loading policy mappings from S3: s3://{POLICY_BUCKET}/{POLICY_MAPPING_KEY}")
        response = s3.get_object(Bucket=POLICY_BUCKET, Key=POLICY_MAPPING_KEY)
        config_content = response['Body'].read().decode('utf-8')
        config = json.loads(config_content)
        
        _policy_mappings_cache = config
        print(f"✓ Loaded {len(config.get('mappings', []))} policy mappings from S3")
        
        return config
    except Exception as e:
        print(f"⚠ Failed to load policy mappings from S3: {e}")
        # Return default config if loading fails
        return {
            'mappings': [],
            'default_policy': DEFAULT_POLICY_KEY
        }


def select_policy_for_finding(finding: Dict[str, Any]) -> Optional[str]:
    """
    Dynamically select appropriate Cloud Custodian policy based on finding characteristics
    Returns the policy key or None if no match found
    """
    config = load_policy_mappings()
    mappings = config.get('mappings', [])
    
    # Extract finding attributes
    source = finding.get('source', '').lower().replace('aws.', '')
    resource_type = finding.get('resource_type', '').upper()
    finding_type = finding.get('finding_type', '').lower()
    event_name = finding.get('raw_event', {}).get('detail', {}).get('eventName', '').lower()
    
    print(f"Policy selection criteria:")
    print(f"  source={source}, resource_type={resource_type}")
    print(f"  finding_type={finding_type}, event_name={event_name}")
    
    # Iterate through mappings and find first match
    for mapping in mappings:
        map_sources = [s.lower().replace('aws.', '') for s in mapping.get('source', [])]
        map_resource_types = [rt.upper() for rt in mapping.get('resource_type', [])]
        map_event_names = [en.lower() for en in mapping.get('event_name', [])]
        map_finding_types = [ft.lower() for ft in mapping.get('finding_type', [])]
        
        # Check if source matches
        source_match = not map_sources or source in map_sources
        
        # Check if resource_type matches
        resource_match = not map_resource_types or resource_type in map_resource_types
        
        # Check if event_name matches
        event_match = not map_event_names or event_name in map_event_names
        
        # Check if finding_type matches (with wildcard support)
        finding_match = not map_finding_types or any(
            matches_pattern(finding_type, pattern) for pattern in map_finding_types
        )
        
        # If all criteria match, return the policy
        if source_match and resource_match and event_match and finding_match:
            policy_file = mapping.get('policy_file')
            print(f"✓ Policy matched: {mapping.get('name', 'Unknown')} -> {policy_file}")
            return policy_file
    
    print(f"⚠ No policy match found")
    return None


def matches_pattern(text: str, pattern: str) -> bool:
    """
    Check if text matches pattern (supports wildcards with *)
    """
    if pattern == '*':
        return True
    
    if '*' in pattern:
        # Remove asterisks and check if pattern text is in the text
        pattern_text = pattern.replace('*', '')
        return pattern_text in text
    
    return text == pattern


