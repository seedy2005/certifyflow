"""Isolate application state and auth settings for the test suite."""
import os
import tempfile

os.environ.setdefault("CERTIBOT_DATA_DIR", tempfile.mkdtemp(prefix="certifyflow-tests-"))
os.environ.setdefault("CERTIBOT_ENV", "test")
os.environ.setdefault("CERTIBOT_SESSION_SECRET", "test-only-session-secret-that-is-long-enough-123456789")
os.environ.setdefault("CERTIBOT_ADMIN_USERNAME", "test-organizer")
os.environ.setdefault("CERTIBOT_ADMIN_PASSWORD", "test-only-password-value-123456")
os.environ.setdefault("CERTIBOT_COOKIE_SECURE", "false")
os.environ.setdefault("CERTIBOT_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")
os.environ.setdefault("CERTIBOT_CORS_ORIGINS", "")
