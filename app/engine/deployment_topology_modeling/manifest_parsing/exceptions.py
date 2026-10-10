class ManifestParsingError(Exception):
    """Base exception for manifest parsing errors."""
    pass

class InvalidTopologyConstraintError(ManifestParsingError):
    """Raised when topological constraints violate schema definitions."""
    pass

class MissingSchemaError(ManifestParsingError):
    """Raised when critical schema nodes are missing."""
    pass
