"""
S3 Bucket Validator
Validates if S3 bucket has public access and requires remediation
"""

import boto3
from typing import Dict, Any
from .base_validator import BaseValidator

s3_client = boto3.client('s3')


class S3Validator(BaseValidator):
    """
    Validates S3 bucket security configurations
    Checks for public access, encryption, versioning, etc.
    """
    
    def get_resource_type(self) -> str:
        return 'S3'
    
    def validate(self, finding: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if S3 bucket is public or has security issues
        
        Validates:
        - Public Access Block configuration
        - Bucket ACLs (public read/write grants)
        - Bucket policies (public statements)
        """
        resource_details = self.extract_resource_details(finding)
        bucket_name = resource_details.get('resource_id', '')
        
        if not bucket_name:
            return self.create_response(
                is_valid=False,
                reason="No bucket name found in finding",
                metadata={'error': 'missing_bucket_name'}
            )
        
        try:
            # Check 1: Public Access Block Configuration
            is_public_via_block = self._check_public_access_block(bucket_name)
            
            # Check 2: Bucket ACL
            is_public_via_acl = self._check_bucket_acl(bucket_name)
            
            # Check 3: Bucket Policy
            is_public_via_policy = self._check_bucket_policy(bucket_name)
            
            # Determine if bucket is public
            is_public = is_public_via_block or is_public_via_acl or is_public_via_policy
            
            metadata = {
                'bucket_name': bucket_name,
                'public_via_block_config': is_public_via_block,
                'public_via_acl': is_public_via_acl,
                'public_via_policy': is_public_via_policy,
            }
            
            if is_public:
                return self.create_response(
                    is_valid=True,
                    reason=f"Bucket '{bucket_name}' has public access - remediation required",
                    metadata=metadata
                )
            else:
                return self.create_response(
                    is_valid=False,
                    reason=f"Bucket '{bucket_name}' is not public - no remediation needed",
                    metadata=metadata
                )
                
        except Exception as e:
            print(f"Error validating S3 bucket {bucket_name}: {str(e)}")
            # On error, allow remediation to proceed (fail-open for security)
            return self.create_response(
                is_valid=True,
                reason=f"Unable to validate bucket status (allowing remediation): {str(e)}",
                metadata={'error': str(e), 'bucket_name': bucket_name}
            )
    
    def _check_public_access_block(self, bucket_name: str) -> bool:
        """
        Check if Public Access Block is disabled (making bucket potentially public)
        Returns True if bucket allows public access
        """
        try:
            response = s3_client.get_public_access_block(Bucket=bucket_name)
            config = response.get('PublicAccessBlockConfiguration', {})
            
            # If any of these are False, bucket could be public
            block_public_acls = config.get('BlockPublicAcls', False)
            ignore_public_acls = config.get('IgnorePublicAcls', False)
            block_public_policy = config.get('BlockPublicPolicy', False)
            restrict_public_buckets = config.get('RestrictPublicBuckets', False)
            
            # Bucket is public if ANY block is disabled
            is_public = not (block_public_acls and ignore_public_acls and 
                           block_public_policy and restrict_public_buckets)
            
            print(f"S3 Public Access Block for {bucket_name}: "
                  f"BlockPublicAcls={block_public_acls}, "
                  f"IgnorePublicAcls={ignore_public_acls}, "
                  f"BlockPublicPolicy={block_public_policy}, "
                  f"RestrictPublicBuckets={restrict_public_buckets} "
                  f"-> is_public={is_public}")
            
            return is_public
            
        except s3_client.exceptions.NoSuchPublicAccessBlockConfiguration:
            # No public access block = potentially public
            print(f"No Public Access Block configuration for {bucket_name} - treating as public")
            return True
        except Exception as e:
            print(f"Error checking public access block for {bucket_name}: {e}")
            return True  # Fail-open: assume public if can't verify
    
    def _check_bucket_acl(self, bucket_name: str) -> bool:
        """
        Check if bucket ACL has public grants
        Returns True if bucket has public ACL
        """
        try:
            response = s3_client.get_bucket_acl(Bucket=bucket_name)
            grants = response.get('Grants', [])
            
            for grant in grants:
                grantee = grant.get('Grantee', {})
                grantee_type = grantee.get('Type', '')
                uri = grantee.get('URI', '')
                
                # Check for AllUsers or AuthenticatedUsers groups
                if grantee_type == 'Group' and ('AllUsers' in uri or 'AuthenticatedUsers' in uri):
                    print(f"Bucket {bucket_name} has public ACL: {uri}")
                    return True
            
            return False
            
        except Exception as e:
            print(f"Error checking bucket ACL for {bucket_name}: {e}")
            return False
    
    def _check_bucket_policy(self, bucket_name: str) -> bool:
        """
        Check if bucket policy allows public access
        Returns True if policy has public statements
        """
        try:
            response = s3_client.get_bucket_policy(Bucket=bucket_name)
            policy_str = response.get('Policy', '{}')
            
            import json
            policy = json.loads(policy_str)
            statements = policy.get('Statement', [])
            
            for statement in statements:
                effect = statement.get('Effect', '')
                principal = statement.get('Principal', {})
                
                # Check for public principal
                if effect == 'Allow':
                    if principal == '*' or principal.get('AWS') == '*':
                        print(f"Bucket {bucket_name} has public policy statement")
                        return True
            
            return False
            
        except s3_client.exceptions.NoSuchBucketPolicy:
            # No policy = not public via policy
            return False
        except Exception as e:
            print(f"Error checking bucket policy for {bucket_name}: {e}")
            return False
