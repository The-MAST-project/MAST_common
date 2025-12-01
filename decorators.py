"""
Decorators for marking backend endpoints as GUI-accessible.
This file is part of MAST_common and shared across all MAST projects.
"""

from functools import wraps
from typing import Callable

def gui_endpoint(
    capability: str | None = None,
    description: str = "",
    rate_limit: int | None = None
):
    """
    Mark a backend endpoint as accessible from the GUI.
    
    Args:
        capability: Required MAST capability (e.g., 'canView', 'canUseControls')
                   None means accessible to all authenticated users
        description: Human-readable description for documentation
        rate_limit: Optional rate limit (requests per minute)
    
    Example:
        @gui_endpoint(capability='canView', description='Get unit status')
        async def unit_status(self, unit_name: str):
            return await self.get(f'unit/{unit_name}/status')
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        
        # Mark the function with GUI metadata
        wrapper._gui_exposed = True
        wrapper._gui_capability = capability
        wrapper._gui_description = description
        wrapper._gui_rate_limit = rate_limit
        
        return wrapper
    return decorator


def is_gui_endpoint(func: Callable) -> bool:
    """Check if a function is marked as GUI-accessible"""
    return getattr(func, '_gui_exposed', False)


def get_endpoint_capability(func: Callable) -> str | None:
    """Get the required capability for a GUI endpoint"""
    return getattr(func, '_gui_capability', None)


def get_endpoint_description(func: Callable) -> str:
    """Get the description of a GUI endpoint"""
    return getattr(func, '_gui_description', '')
