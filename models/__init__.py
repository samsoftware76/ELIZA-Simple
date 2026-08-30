# This file makes the 'models' directory a Python package.
# Import all models to make them accessible when importing from the models package

from .models import db, User, Client, Project, Task, Comment, ActivityLog, ProjectMember, TimeEntry
from .models import ProjectStatus, TaskStatus, UserRole
# Note: Quote/Invoice models live in models.billing and are imported directly from
# there (matching how models.subscription is handled) to avoid a circular import
# with `from models import db` inside billing.py.
