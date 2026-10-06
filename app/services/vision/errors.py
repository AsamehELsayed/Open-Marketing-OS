"""Vision service errors (DEV-007 Phase B / W3-impl)."""


class VisionProviderError(RuntimeError):
    """Real backend cannot run (missing deps, weights, or inference failure)."""


class VisionNotConfiguredError(VisionProviderError):
    """Optional provider is not configured (e.g. no vault ref for OpenAI)."""


class VisionIngressError(ValueError):
    """Image ingress failed closed (missing project, file, or kind mismatch)."""
