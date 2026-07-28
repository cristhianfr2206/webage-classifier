import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-thirty-two-bytes")
os.environ.setdefault("INITIAL_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "long-test-password")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://testserver"]')
os.environ.setdefault("ALLOWED_HOSTS", '["testserver"]')
