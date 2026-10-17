# Where the routers find the services.
#
# main.py builds both services once at startup and puts them here. Routers read them straight from this module, so no request pays to look them up.
from app.analytics.service import AnalyticsService
from app.transactions.service import TransactionService

# Declared but not assigned: main.py fills these in before any request arrives. Reading them before that would be an error, which is what we want.
transactions: TransactionService
analytics: AnalyticsService
