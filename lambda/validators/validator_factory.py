"""
Validator Factory
Dynamically selects and instantiates the appropriate validator based on resource type
Currently only S3 validation is enabled - all other resources proceed without validation
"""

from typing import Dict, Any, Optional
from .base_validator import BaseValidator
from .s3_validator import S3Validator


class ValidatorFactory:
    """
    Factory class to create validators based on resource type
    """
    
    # Registry of validators by resource type
    # Currently only S3 validation is enabled
    _validators = {
        'S3': S3Validator,
    }
    
    @classmethod
    def get_validator(cls, resource_type: str) -> Optional[BaseValidator]:
        """
        Get validator instance for the specified resource type
        
        Args:
            resource_type: Resource type (e.g., 'S3', 'EC2', 'IAM')
            
        Returns:
            BaseValidator instance or None if no validator found
        """
        resource_type_upper = resource_type.upper()
        
        validator_class = cls._validators.get(resource_type_upper)
        
        if validator_class:
            print(f"✓ Found validator for resource type: {resource_type_upper}")
            return validator_class()
        else:
            print(f"⚠ No validator registered for resource type: {resource_type_upper}")
            return None
    
    @classmethod
    def validate_finding(cls, finding: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convenience method to validate a finding
        Automatically selects the correct validator and runs validation
        
        Args:
            finding: Parsed security finding
            
        Returns:
            Validation result dict with:
                - is_valid: bool (True if should be remediated)
                - reason: str (explanation)
                - metadata: dict (additional context)
        """
        resource_type = finding.get('resource_type', '').upper()
        
        if not resource_type:
            return {
                'is_valid': False,
                'reason': 'No resource type found in finding',
                'metadata': {'error': 'missing_resource_type'},
                'validator': 'ValidatorFactory'
            }
        
        validator = cls.get_validator(resource_type)
        
        if not validator:
            # No specific validator - allow remediation for all non-S3 resources
            print(f"No validator for {resource_type} - allowing remediation (validation only enabled for S3)")
            return {
                'is_valid': True,
                'reason': f'Validation not enabled for {resource_type} - allowing remediation',
                'metadata': {'resource_type': resource_type, 'validation_enabled': False},
                'validator': 'ValidatorFactory'
            }
        
        # Run validation
        try:
            result = validator.validate(finding)
            print(f"Validation result for {resource_type}: {result.get('reason')}")
            return result
        except Exception as e:
            print(f"Error during validation: {str(e)}")
            # Fail-open: allow remediation on error
            return {
                'is_valid': True,
                'reason': f'Validation error (allowing remediation): {str(e)}',
                'metadata': {'error': str(e), 'resource_type': resource_type},
                'validator': validator.__class__.__name__
            }
    
    @classmethod
    def register_validator(cls, resource_type: str, validator_class: type) -> None:
        """
        Register a new validator for a resource type
        Allows for dynamic extension of validators
        
        Args:
            resource_type: Resource type (e.g., 'RDS', 'Lambda')
            validator_class: Validator class (must inherit from BaseValidator)
        """
        if not issubclass(validator_class, BaseValidator):
            raise TypeError(f"{validator_class} must inherit from BaseValidator")
        
        cls._validators[resource_type.upper()] = validator_class
        print(f"Registered validator for {resource_type}: {validator_class.__name__}")
    
    @classmethod
    def list_validators(cls) -> list:
        """Return list of registered resource types"""
        return list(cls._validators.keys())
