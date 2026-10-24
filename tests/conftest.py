"""Isolate application state and auth settings for the test suite."""
import os
import tempfile

os.environ.setdefault("CERTIFYFLOW_DATA_DIR", tempfile.mkdtemp(prefix="certifyflow-tests-"))
os.environ.setdefault("CERTIFYFLOW_ENV", "test")
os.environ.setdefault("CERTIFYFLOW_SESSION_SECRET", "test-only-session-secret-that-is-long-enough-123456789")
os.environ.setdefault("CERTIFYFLOW_ADMIN_USERNAME", "test-organizer")
os.environ.setdefault("CERTIFYFLOW_ADMIN_PASSWORD", "test-only-password-value-123456")
os.environ.setdefault("CERTIFYFLOW_COOKIE_SECURE", "false")
os.environ.setdefault("CERTIFYFLOW_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("CERTIFYFLOW_CORS_ORIGINS", "")
