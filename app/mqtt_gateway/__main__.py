import asyncio
import logging

# Load .env before any app imports so settings are populated in local dev.
from dotenv import load_dotenv
load_dotenv()

from app.core.app_logging import setup_logging
from app.mqtt_gateway.gateway import run

setup_logging()
logger = logging.getLogger("mqtt_gateway")

if __name__ == "__main__":
    logger.info("Boboloo MQTT Gateway starting...")
    asyncio.run(run())
