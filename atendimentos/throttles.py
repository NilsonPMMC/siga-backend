from rest_framework.throttling import UserRateThrottle


class EnrichmentPreviewThrottle(UserRateThrottle):
    scope = "enrichment_preview"


class EnrichmentApplyThrottle(UserRateThrottle):
    scope = "enrichment_apply"

