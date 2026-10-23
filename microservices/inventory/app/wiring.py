# Where this service's routes find its service object.
#
# main.py builds it once at startup and puts it here. Routes read it straight from this module, so no request pays to look it up.
from app.service import InventoryService
from app.stock_mover import StockMover

# Declared but not assigned: main.py fills these in before any request arrives.
service: InventoryService        # the two public read endpoints
stock_mover: StockMover          # the internal write path, used only by checkout during a payment
