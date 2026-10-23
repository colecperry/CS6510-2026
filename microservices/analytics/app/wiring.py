# Where this service's routes find its service object.
#
# main.py builds it once at startup and puts it here. Routes read it straight from this module, so no request pays to look it up.
from app.data.scans_repo import ScansRepo
from app.service import AnalyticsService

# Declared but not assigned: main.py fills these in before any request arrives.
service: AnalyticsService    # the public ranking endpoint
scans: ScansRepo             # the internal intake, written to only by checkout
