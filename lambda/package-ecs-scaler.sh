#!/bin/bash
# Package ECS Scaler Lambda function

echo "Packaging ECS Scaler Lambda function..."

cd lambda

# Remove old package if exists
rm -f ecs-scaler-function.zip

# Create zip with just the scaler file (no dependencies needed)
zip ecs-scaler-function.zip ecs_scaler.py

echo "✓ Package created: lambda/ecs-scaler-function.zip"
ls -lh ecs-scaler-function.zip
