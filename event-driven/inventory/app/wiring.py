# Where this service's routes find its service object.
#
# main.py builds it once at startup and puts it here. Routes read it straight from this module, so no request pays to look it up.
from app.service import InventoryService

# Declared but not assigned: main.py fills this in before any request arrives.
# The stock mover used to be here too, because an HTTP route reached it. Nothing reaches it from a request any more, so it is held by the consumer alone.
service: InventoryService
