"""
ECS Auto-Scaler Lambda Function
Triggers immediately when messages arrive in SQS queue to scale ECS service
"""

import boto3
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ecs_client = boto3.client('ecs')
cloudwatch = boto3.client('cloudwatch')

CLUSTER_NAME = os.environ['ECS_CLUSTER_NAME']
SERVICE_NAME = os.environ['ECS_SERVICE_NAME']
MIN_TASKS = int(os.environ.get('MIN_TASKS', '0'))
MAX_TASKS = int(os.environ.get('MAX_TASKS', '10'))
MESSAGES_PER_TASK = int(os.environ.get('MESSAGES_PER_TASK', '5'))


def get_current_desired_count():
    """Get current desired count of ECS service"""
    try:
        response = ecs_client.describe_services(
            cluster=CLUSTER_NAME,
            services=[SERVICE_NAME]
        )
        
        if response['services']:
            return response['services'][0]['desiredCount']
        return 0
    except Exception as e:
        logger.error(f"Error getting desired count: {e}")
        return 0


def calculate_desired_tasks(message_count):
    """Calculate desired task count based on message count"""
    if message_count == 0:
        return MIN_TASKS
    
    # Calculate tasks needed: ceil(messages / messages_per_task)
    desired = -(-message_count // MESSAGES_PER_TASK)  # Ceiling division
    
    # Ensure within bounds
    desired = max(MIN_TASKS, min(desired, MAX_TASKS))
    
    return desired


def update_ecs_service(desired_count):
    """Update ECS service desired count"""
    try:
        response = ecs_client.update_service(
            cluster=CLUSTER_NAME,
            service=SERVICE_NAME,
            desiredCount=desired_count
        )
        
        logger.info(f"Updated ECS service to {desired_count} tasks")
        return response
    except Exception as e:
        logger.error(f"Error updating ECS service: {e}")
        raise


def publish_metric(metric_name, value, unit='Count'):
    """Publish custom metric to CloudWatch"""
    try:
        cloudwatch.put_metric_data(
            Namespace='CloudCustodian/ECS',
            MetricData=[
                {
                    'MetricName': metric_name,
                    'Value': value,
                    'Unit': unit,
                    'Timestamp': datetime.utcnow()
                }
            ]
        )
    except Exception as e:
        logger.error(f"Error publishing metric {metric_name}: {e}")


def lambda_handler(event, context):
    """
    Lambda handler triggered by SQS queue
    Scales ECS service based on approximate message count
    """
    try:
        logger.info(f"ECS Scaler triggered with event: {json.dumps(event)}")
        
        # Get approximate message count from SQS event
        # The event contains Records array with messages
        record_count = len(event.get('Records', []))
        
        logger.info(f"Received {record_count} records from SQS")
        
        # Get current desired count
        current_desired = get_current_desired_count()
        logger.info(f"Current ECS desired count: {current_desired}")
        
        # For immediate scaling: if we have any messages and desired count is 0, scale to 1
        # This ensures immediate task startup
        if record_count > 0 and current_desired == 0:
            desired_count = 1  # Start with 1 task immediately
            logger.info(f"Scaling from 0 to 1 task immediately")
            
            update_ecs_service(desired_count)
            publish_metric('ECSScalerTriggered', 1)
            publish_metric('ECSDesiredCount', desired_count)
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'ECS service scaled successfully',
                    'previousCount': current_desired,
                    'newCount': desired_count,
                    'recordCount': record_count
                })
            }
        else:
            logger.info(f"No scaling needed. Current: {current_desired}, Records: {record_count}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No scaling action needed',
                    'currentCount': current_desired,
                    'recordCount': record_count
                })
            }
            
    except Exception as e:
        logger.error(f"Error in ECS scaler: {e}", exc_info=True)
        publish_metric('ECSScalerErrors', 1)
        raise
