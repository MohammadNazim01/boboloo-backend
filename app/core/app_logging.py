import logging
import sys


def setup_logging():

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        handlers=[
            logging.StreamHandler(sys.stdout)
        ],
        force=True,
    )

    # Reduce noisy libraries
    logging.getLogger("uvicorn.access").setLevel(
        logging.WARNING
    )
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.WARNING
    )