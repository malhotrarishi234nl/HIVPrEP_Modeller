"""Browser interface support for the local model workbench.

The package deliberately contains no simulation code.  ``app.py`` renders the
Streamlit interface and ``service.py`` adapts repository metadata and the
optional ``model_workbench`` service package into small, testable values.
"""

from .service import AnalysisDraft, UIWorkbenchService

__all__ = ["AnalysisDraft", "UIWorkbenchService"]
