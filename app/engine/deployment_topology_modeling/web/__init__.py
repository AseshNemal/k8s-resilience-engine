"""
Web helpers for Component 2 (Deployment Topology Modeling).
Provides server-side rendering context assembly and presentation formatting.
"""
from .dashboard_context_builder import DashboardContextBuilder
from .academic_metric_presenter import AcademicMetricPresenter

__all__ = ["DashboardContextBuilder", "AcademicMetricPresenter"]
