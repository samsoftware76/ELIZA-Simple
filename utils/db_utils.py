"""
Database utilities for the ELIZA Project Management App
"""
import time
import logging
from sqlalchemy import exc
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)

# Import the existing db instance from models
from models import db

# Configure the SQLAlchemy pool settings
def configure_db_pool(app):
    """Configure database connection pool settings"""
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'poolclass': QueuePool,
        'pool_size': 10,
        'max_overflow': 20,
        'pool_timeout': 30,
        'pool_recycle': 1800,  # Recycle connections after 30 minutes
        'pool_pre_ping': True  # Check connection validity before using it
    }

# Retry logic for database operations
def retry_operation(operation, max_retries=3, retry_delay=0.5):
    """
    Retry a database operation with exponential backoff
    
    Args:
        operation: Callable that performs the database operation
        max_retries: Maximum number of retries
        retry_delay: Initial delay between retries (will increase exponentially)
        
    Returns:
        The result of the operation
        
    Raises:
        The last exception encountered if all retries fail
    """
    retries = 0
    last_error = None
    
    while retries <= max_retries:
        try:
            return operation()
        except (exc.OperationalError, exc.TimeoutError, exc.ResourceClosedError) as e:
            last_error = e
            retries += 1
            
            if retries <= max_retries:
                # Log the error and retry
                logger.warning(f"Database operation failed (attempt {retries}/{max_retries}): {str(e)}")
                # Exponential backoff
                time.sleep(retry_delay * (2 ** (retries - 1)))
                
                # Force a new connection on the next attempt
                db.engine.dispose()
            else:
                # Log the final failure
                logger.error(f"Database operation failed after {max_retries} retries: {str(e)}")
                raise
        except Exception as e:
            # Don't retry other types of exceptions
            logger.error(f"Database operation failed with non-retryable error: {str(e)}")
            raise
            
    # This should not be reached, but just in case
    if last_error:
        raise last_error
    raise Exception("Unknown error in retry_operation")

# Helper functions for common database operations
def safe_commit(session, max_retries=3):
    """
    Safely commit changes to the database with retry logic
    
    Args:
        session: SQLAlchemy session
        max_retries: Maximum number of retries
        
    Returns:
        True if commit succeeded, False otherwise
    """
    def commit_operation():
        session.commit()
        return True
        
    try:
        return db.retry_operation(commit_operation, max_retries=max_retries)
    except Exception as e:
        logger.error(f"Failed to commit changes to database: {str(e)}")
        session.rollback()
        return False

def safe_query(query_func, max_retries=3):
    """
    Safely execute a database query with retry logic
    
    Args:
        query_func: Function that returns a SQLAlchemy query
        max_retries: Maximum number of retries
        
    Returns:
        Query results or None if query failed
    """
    try:
        return db.retry_operation(query_func, max_retries=max_retries)
    except Exception as e:
        logger.error(f"Failed to execute database query: {str(e)}")
        return None
