# Where the routers find the services.
#
# main.py builds both services once at startup and drops them here. Routers
# read them directly rather than going through FastAPI's Depends, which
# would rebuild the lookup on every request for objects that never change.
from app.analytics.service import AnalyticsService
from app.transactions.service import TransactionService

transactions: TransactionService
analytics: AnalyticsService
