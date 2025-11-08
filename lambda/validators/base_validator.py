"""
Base Validator Class
Abstract base class for all resource validators
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseValidator(ABC):
    """
    Abstract base class for resource validators
    Each validator checks if a security event requires remediation
    """
    
    def __init__(self):
        self.resource_type = self.get_resource_type()
    
    @abstractmethod
    def get_resource_type(self) -> str:
        """Return the resource type this validator handles (e.g., 'S3', 'EC2', 'IAM')"""
        pass
    
    @abstractmethod
    def validate(self, finding: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate if the finding requires remediation
        
        Args:
            finding: Parsed security finding with resource details
            
        Returns:
            Dict with:
                - is_valid: bool (True if should be remediated)
                - reason: str (explanation)
                - metadata: dict (additional context)
        """
        pass
    
    def extract_resource_details(self, finding: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract common resource details from finding
        Override in subclass for resource-specific extraction
        """
        return {
            'resource_id': finding.get('resource_id', ''),
            'resource_arn': finding.get('resource_arn', ''),
            'resource_type': finding.get('resource_type', ''),
            'region': finding.get('region', ''),
            'account': finding.get('account', ''),
        }
    
    def create_response(self, is_valid: bool, reason: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Helper to create standardized validation response"""
        return {
            'is_valid': is_valid,
            'reason': reason,
            'metadata': metadata or {},
            'validator': self.__class__.__name__
        }
