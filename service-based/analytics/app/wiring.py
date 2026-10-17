# Where this service's routes find its service object.
#
# main.py builds it once at startup and puts it here. Routes read it straight from this module, so no request pays to look it up.
from app.service import AnalyticsService

# Declared but not assigned: main.py fills this in before any request arrives.
service: AnalyticsService
