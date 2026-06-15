"""
Serviços para o módulo de atendimentos.
"""
from .google_calendar_compatibility import (
    GoogleCalendarCompatibilityService,
    GoogleCalendarAuthError,
    GoogleCalendarPermissionError
)

__all__ = [
    'GoogleCalendarCompatibilityService',
    'GoogleCalendarAuthError', 
    'GoogleCalendarPermissionError'
]