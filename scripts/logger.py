import logging
import os
from datetime import datetime
import pytz

LOGS_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
EASTERN = pytz.timezone('US/Eastern')


def get_logger(name):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Daily file handler (date in US/Eastern)
    today = datetime.now(EASTERN).strftime('%Y-%m-%d')
    log_path = os.path.abspath(os.path.join(LOGS_DIR, f'{today}.log'))
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
