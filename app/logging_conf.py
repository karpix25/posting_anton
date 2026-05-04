import logging
import sys
from pythonjsonlogger import jsonlogger
from logging.handlers import RotatingFileHandler

def setup_logging():
    """Configures the root logger to output JSON structured logs."""
    logger = logging.getLogger()
    
    # Remove existing handlers to avoid duplication if re-called
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Console handler (JSON format for Docker/EasyPanel logs)
    console_handler = logging.StreamHandler(sys.stdout)
    json_formatter = jsonlogger.JsonFormatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s',
        rename_fields={'asctime': 'timestamp', 'levelname': 'level'}
    )
    console_handler.setFormatter(json_formatter)
    logger.addHandler(console_handler)
    
    # File handler (plain text format for UI logs tab)
    try:
        file_handler = RotatingFileHandler(
            '/tmp/app.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3
        )
        plain_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(plain_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # Silently fail if can't write to /tmp (e.g., permissions)
        print(f"Warning: Could not set up file logging: {e}")
    
    logger.setLevel(logging.INFO)
    
    # Set levels for noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

    return logger
