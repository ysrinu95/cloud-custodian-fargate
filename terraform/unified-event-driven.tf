# ============================================================================
# Cloud Custodian - Unified Event-Driven Architecture
# ============================================================================
# Based on the architecture diagram: Single EventBridge → Lambda → SQS → ECS
# ============================================================================

terraform {
  required_version = ">= 1.0"
  
  # S3 Backend for Remote State (no DynamoDB locking)
  backend "s3" {
    bucket  = "ysr95-cloud-custodian-tf-bkt"
    key     = "terraform/unified-event-driven/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
  
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ============================================================================
# PROVIDER
# ============================================================================

provider "aws" {
  region = var.aws_region
  
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ============================================================================
# VARIABLES
# ============================================================================

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "cloud-custodian"
}

variable "environment" {
  description = "Environment"
  type        = string
  default     = "production"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "enable_ecs_worker" {
  description = "Enable ECS Fargate worker (set to false for Lambda-only approach)"
  type        = bool
  default     = true
}

variable "ecs_task_cpu" {
  description = "ECS task CPU units (256, 512, 1024, 2048, 4096)"
  type        = string
  default     = "512"
}

variable "ecs_task_memory" {
  description = "ECS task memory in MB (512, 1024, 2048, 4096, 8192)"
  type        = string
  default     = "1024"
}

variable "policy_key" {
  description = "S3 key path to the Cloud Custodian policy file (used as default fallback)"
  type        = string
  default     = "policies/s3-createbucket.yml"
}

variable "policy_mapping_key" {
  description = "S3 key path to the policy mapping configuration file"
  type        = string
  default     = "config/policy-mappings.json"
}

# ============================================================================
# DATA SOURCES
# ============================================================================

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Get default VPC and subnets
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ============================================================================
# S3 BUCKET - Policy Storage and Outputs
# ============================================================================

resource "aws_s3_bucket" "custodian_bucket" {
  bucket = "${var.project_name}-unified-${data.aws_caller_identity.current.account_id}"
  
  tags = {
    Name        = "Cloud Custodian Unified Bucket"
    Purpose     = "Policy storage and outputs"
    Environment = var.environment
  }
}

resource "aws_s3_bucket_versioning" "custodian_bucket" {
  bucket = aws_s3_bucket.custodian_bucket.id
  
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "custodian_bucket" {
  bucket = aws_s3_bucket.custodian_bucket.id
  
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "custodian_bucket" {
  bucket = aws_s3_bucket.custodian_bucket.id
  
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ============================================================================
# REMOVED RESOURCES SECTION (OLD COMMENT BLOCK)
# ============================================================================
# The following resources have been removed:
# - SQS Queue and DLQ
# - ECR Repository
# - Lambda Function and IAM Role
# - ECS Cluster, Service, Task Definition
# - ECS IAM Roles
# - Security Groups
# - EventBridge Rules
# - CloudWatch Log Groups
#
# Resources are now active below (uncommented)
# ============================================================================

# ============================================================================
# SQS QUEUE - Security Findings Queue
# ============================================================================

resource "aws_sqs_queue" "custodian_dlq" {
  name                      = "${var.project_name}-dlq"
  message_retention_seconds = 1209600  # 14 days
  sqs_managed_sse_enabled   = true
  
  tags = {
    Name        = "Cloud Custodian DLQ"
    Purpose     = "Dead letter queue for failed messages"
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "custodian_queue" {
  name                       = "${var.project_name}-queue"
  visibility_timeout_seconds = 3600  # 1 hour (must be >= Lambda/ECS task timeout)
  message_retention_seconds  = 345600  # 4 days
  receive_wait_time_seconds  = 20  # Long polling
  sqs_managed_sse_enabled    = true
  
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.custodian_dlq.arn
    maxReceiveCount     = 3
  })
  
  tags = {
    Name        = "Cloud Custodian Queue"
    Purpose     = "Buffer security findings for processing"
    Environment = var.environment
  }
}

# ============================================================================
# ECR REPOSITORY - Worker Docker Images
# ============================================================================

resource "aws_ecr_repository" "worker" {
  count                = var.enable_ecs_worker ? 1 : 0
  name                 = "${var.project_name}-worker"
  image_tag_mutability = "MUTABLE"
  
  image_scanning_configuration {
    scan_on_push = true
  }
  
  tags = {
    Name        = "Cloud Custodian Worker"
    Purpose     = "ECS Fargate worker container images"
    Environment = var.environment
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  count      = var.enable_ecs_worker ? 1 : 0
  repository = aws_ecr_repository.worker[0].name
  
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus     = "any"
        countType     = "imageCountMoreThan"
        countNumber   = 10
      }
      action = {
        type = "expire"
      }
    }]
  })
}

# ============================================================================
# IAM ROLE - Lambda Invoker
# ============================================================================

resource "aws_iam_role" "lambda_invoker" {
  name = "${var.project_name}-invoker-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
  
  tags = {
    Name = "Lambda Invoker Role"
  }
}

resource "aws_iam_role_policy" "lambda_invoker" {
  name = "${var.project_name}-invoker-policy"
  role = aws_iam_role.lambda_invoker.id
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-invoker:*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueUrl",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.custodian_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = [
          "${aws_s3_bucket.custodian_bucket.arn}/config/*",
          "${aws_s3_bucket.custodian_bucket.arn}/events/*",
          "${aws_s3_bucket.custodian_bucket.arn}/policies/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:DescribeTasks"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          var.enable_ecs_worker ? aws_iam_role.ecs_task_execution[0].arn : "",
          var.enable_ecs_worker ? aws_iam_role.ecs_task[0].arn : ""
        ]
      }
    ]
  })
}

# ============================================================================
# LAMBDA FUNCTION - Invoker
# ============================================================================

resource "aws_lambda_function" "invoker" {
  filename         = "${path.module}/../lambda/lambda-function.zip"
  function_name    = "${var.project_name}-invoker"
  role             = aws_iam_role.lambda_invoker.arn
  handler          = "invoker_lambda.lambda_handler"
  source_code_hash = fileexists("${path.module}/../lambda/lambda-function.zip") ? filebase64sha256("${path.module}/../lambda/lambda-function.zip") : null
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 256
  
  environment {
    variables = {
      SQS_QUEUE_URL        = aws_sqs_queue.custodian_queue.url
      S3_BUCKET            = aws_s3_bucket.custodian_bucket.id
      ENABLE_ENRICHMENT    = "true"
      POLICY_BUCKET        = aws_s3_bucket.custodian_bucket.id
      POLICY_MAPPING_KEY   = var.policy_mapping_key
      DEFAULT_POLICY_KEY   = var.policy_key
      ECS_CLUSTER          = var.enable_ecs_worker ? aws_ecs_cluster.main[0].name : ""
      ECS_TASK_DEFINITION  = var.enable_ecs_worker ? aws_ecs_task_definition.worker[0].family : ""
      ECS_SUBNETS          = join(",", data.aws_subnets.default.ids)
      ECS_SECURITY_GROUP   = var.enable_ecs_worker ? aws_security_group.ecs_worker[0].id : ""
    }
  }
  
  tags = {
    Name        = "Cloud Custodian Invoker"
    Purpose     = "Parse events and enqueue to SQS"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_log_group" "lambda_invoker" {
  name              = "/aws/lambda/${var.project_name}-invoker"
  retention_in_days = 7
  
  tags = {
    Name = "Lambda Invoker Logs"
  }
}

# ============================================================================
# IAM ROLE - ECS Task Execution
# ============================================================================

resource "aws_iam_role" "ecs_task_execution" {
  count = var.enable_ecs_worker ? 1 : 0
  name  = "${var.project_name}-ecs-execution-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  
  tags = {
    Name = "ECS Task Execution Role"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  count      = var.enable_ecs_worker ? 1 : 0
  role       = aws_iam_role.ecs_task_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ============================================================================
# IAM ROLE - ECS Task (Worker Application)
# ============================================================================

resource "aws_iam_role" "ecs_task" {
  count = var.enable_ecs_worker ? 1 : 0
  name  = "${var.project_name}-ecs-task-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  
  tags = {
    Name = "ECS Task Role"
  }
}

resource "aws_iam_role_policy" "ecs_task" {
  count = var.enable_ecs_worker ? 1 : 0
  name  = "${var.project_name}-ecs-task-policy"
  role  = aws_iam_role.ecs_task[0].id
  
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.custodian_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.custodian_bucket.arn,
          "${aws_s3_bucket.custodian_bucket.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.custodian_bucket.arn}/outputs/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${var.project_name}-worker:*"
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:Terminate*",
          "ec2:Stop*",
          "ec2:CreateTags",
          "s3:GetBucket*",
          "s3:ListBucket*",
          "s3:DeleteBucket",
          "s3:PutBucket*",
          "iam:List*",
          "iam:Get*"
        ]
        Resource = "*"
      }
    ]
  })
}

# ============================================================================
# ECS CLUSTER
# ============================================================================

resource "aws_ecs_cluster" "main" {
  count = var.enable_ecs_worker ? 1 : 0
  name  = "${var.project_name}-cluster"
  
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  
  tags = {
    Name        = "Cloud Custodian Cluster"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_log_group" "ecs_worker" {
  count             = var.enable_ecs_worker ? 1 : 0
  name              = "/ecs/${var.project_name}-worker"
  retention_in_days = 7
  
  tags = {
    Name = "ECS Worker Logs"
  }
}

# ============================================================================
# ECS TASK DEFINITION
# ============================================================================

resource "aws_ecs_task_definition" "worker" {
  count                    = var.enable_ecs_worker ? 1 : 0
  family                   = "${var.project_name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_task_cpu
  memory                   = var.ecs_task_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution[0].arn
  task_role_arn            = aws_iam_role.ecs_task[0].arn
  
  container_definitions = jsonencode([{
    name  = "worker"
    image = "${aws_ecr_repository.worker[0].repository_url}:latest"
    
    environment = [
      {
        name  = "SQS_QUEUE_URL"
        value = aws_sqs_queue.custodian_queue.url
      },
      {
        name  = "AWS_DEFAULT_REGION"
        value = var.aws_region
      },
      {
        name  = "OUTPUT_BUCKET"
        value = aws_s3_bucket.custodian_bucket.id
      },
      {
        name  = "POLICY_BUCKET"
        value = aws_s3_bucket.custodian_bucket.id
      },
      {
        name  = "POLICY_KEY"
        value = var.policy_key
      }
    ]
    
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.ecs_worker[0].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
    
    essential = true
  }])
  
  tags = {
    Name = "Worker Task Definition"
  }
}

# ============================================================================
# ECS SERVICE (Auto-scaling from 0)
# ============================================================================

resource "aws_ecs_service" "worker" {
  count           = var.enable_ecs_worker ? 1 : 0
  name            = "${var.project_name}-worker-${var.environment}"
  cluster         = aws_ecs_cluster.main[0].id
  task_definition = aws_ecs_task_definition.worker[0].arn
  desired_count   = 0  # Start with 0 tasks, scale up based on SQS queue depth
  launch_type     = "FARGATE"
  
  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs_worker[0].id]
    assign_public_ip = true  # Required for Fargate in public subnets
  }
  
  deployment_configuration {
    maximum_percent         = 200
    minimum_healthy_percent = 100
  }
  
  enable_execute_command = true
  
  tags = {
    Name        = "${var.project_name}-worker-${var.environment}"
    Environment = var.environment
    ManagedBy   = "Terraform"
    Purpose     = "Auto-scaling worker for Cloud Custodian policies"
  }
}

# ============================================================================
# ECS AUTO-SCALING
# ============================================================================

# Application Auto Scaling Target
resource "aws_appautoscaling_target" "ecs_target" {
  count              = var.enable_ecs_worker ? 1 : 0
  max_capacity       = 10  # Maximum number of tasks
  min_capacity       = 0   # Can scale down to 0
  resource_id        = "service/${aws_ecs_cluster.main[0].name}/${aws_ecs_service.worker[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# Auto Scaling Policy - Scale based on SQS Queue Depth
resource "aws_appautoscaling_policy" "ecs_policy_scale_up" {
  count              = var.enable_ecs_worker ? 1 : 0
  name               = "${var.project_name}-scale-up-${var.environment}"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs_target[0].resource_id
  scalable_dimension = aws_appautoscaling_target.ecs_target[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs_target[0].service_namespace
  
  target_tracking_scaling_policy_configuration {
    target_value       = 5.0  # Target 5 messages per task
    scale_in_cooldown  = 300  # Wait 5 minutes before scaling down
    scale_out_cooldown = 60   # Wait 1 minute before scaling up again
    
    customized_metric_specification {
      metric_name = "ApproximateNumberOfMessagesVisible"
      namespace   = "AWS/SQS"
      statistic   = "Average"
      
      dimensions {
        name  = "QueueName"
        value = aws_sqs_queue.custodian_queue.name
      }
    }
  }
}

# ============================================================================
# SECURITY GROUP - ECS Worker
# ============================================================================

resource "aws_security_group" "ecs_worker" {
  count       = var.enable_ecs_worker ? 1 : 0
  name        = "${var.project_name}-ecs-sg"
  description = "Security group for ECS worker"
  vpc_id      = data.aws_vpc.default.id
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }
  
  tags = {
    Name = "ECS Worker Security Group"
  }
}

# ============================================================================
# EVENTBRIDGE RULE - Unified Security Findings
# ============================================================================

resource "aws_cloudwatch_event_rule" "unified_security" {
  name        = "${var.project_name}-unified-security"
  description = "Unified rule capturing all security findings"
  
  event_pattern = jsonencode({
    source = [
      "aws.securityhub",
      "aws.guardduty",
      "aws.macie",
      "aws.config",
      "aws.cloudtrail",
      "aws.access-analyzer"
    ]
    detail-type = [
      "Security Hub Findings - Imported",
      "GuardDuty Finding",
      "Macie Finding",
      "Config Rules Compliance Change",
      "AWS API Call via CloudTrail",
      "Access Analyzer Finding"
    ]
  })
  
  tags = {
    Name = "Unified Security Findings Rule"
  }
}

resource "aws_cloudwatch_event_target" "unified_security" {
  rule      = aws_cloudwatch_event_rule.unified_security.name
  target_id = "InvokerLambda"
  arn       = aws_lambda_function.invoker.arn
}

resource "aws_lambda_permission" "unified_security" {
  statement_id  = "AllowEventBridgeUnifiedInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.invoker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.unified_security.arn
}

# ============================================================================
# EVENTBRIDGE RULE - S3 CloudTrail Events (for testing)
# ============================================================================

resource "aws_cloudwatch_event_rule" "s3_events" {
  name        = "${var.project_name}-s3-events"
  description = "Capture S3 bucket creation and policy changes"
  
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        "CreateBucket",
        "PutBucketPolicy",
        "PutBucketAcl",
        "DeleteBucketPolicy",
        "PutBucketPublicAccessBlock"
      ]
    }
  })
  
  tags = {
    Name = "S3 Events Rule"
  }
}

resource "aws_cloudwatch_event_target" "s3_events" {
  rule      = aws_cloudwatch_event_rule.s3_events.name
  target_id = "InvokerLambda"
  arn       = aws_lambda_function.invoker.arn
}

resource "aws_lambda_permission" "s3_events" {
  statement_id  = "AllowEventBridgeS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.invoker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.s3_events.arn
}

# ============================================================================
# EVENTBRIDGE RULE - EC2 CloudTrail Events (for testing)
# ============================================================================

resource "aws_cloudwatch_event_rule" "ec2_events" {
  name        = "${var.project_name}-ec2-events"
  description = "Capture EC2 RunInstances events"
  
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = ["RunInstances"]
    }
  })
  
  tags = {
    Name = "EC2 Events Rule"
  }
}

resource "aws_cloudwatch_event_target" "ec2_events" {
  rule      = aws_cloudwatch_event_rule.ec2_events.name
  target_id = "InvokerLambda"
  arn       = aws_lambda_function.invoker.arn
}

resource "aws_lambda_permission" "ec2_events" {
  statement_id  = "AllowEventBridgeEC2Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.invoker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ec2_events.arn
}

# ============================================================================
# OUTPUTS
# ============================================================================

output "s3_bucket_name" {
  description = "S3 bucket for policies and outputs"
  value       = aws_s3_bucket.custodian_bucket.id
}

output "sqs_queue_url" {
  description = "SQS queue URL"
  value       = aws_sqs_queue.custodian_queue.url
}

output "sqs_queue_arn" {
  description = "SQS queue ARN"
  value       = aws_sqs_queue.custodian_queue.arn
}

output "lambda_function_name" {
  description = "Lambda invoker function name"
  value       = aws_lambda_function.invoker.function_name
}

output "lambda_function_arn" {
  description = "Lambda invoker function ARN"
  value       = aws_lambda_function.invoker.arn
}

output "ecr_repository_url" {
  description = "ECR repository URL for worker images"
  value       = var.enable_ecs_worker ? aws_ecr_repository.worker[0].repository_url : null
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = var.enable_ecs_worker ? aws_ecs_cluster.main[0].name : null
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = var.enable_ecs_worker ? aws_ecs_service.worker[0].name : null
}

output "eventbridge_rule_unified" {
  description = "Unified EventBridge rule name"
  value       = aws_cloudwatch_event_rule.unified_security.name
}

output "eventbridge_rule_s3" {
  description = "S3 EventBridge rule name"
  value       = aws_cloudwatch_event_rule.s3_events.name
}

output "eventbridge_rule_ec2" {
  description = "EC2 EventBridge rule name"
  value       = aws_cloudwatch_event_rule.ec2_events.name
}

output "aws_account_id" {
  description = "AWS account ID"
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "AWS region"
  value       = data.aws_region.current.name
}

output "policy_location" {
  description = "S3 location of the Cloud Custodian policy"
  value       = "s3://${aws_s3_bucket.custodian_bucket.id}/${var.policy_key}"
}
