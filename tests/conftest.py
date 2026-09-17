import os
import tempfile
from pathlib import Path

# Configure isolated storage before importing application modules.
_temporary = tempfile.TemporaryDirectory(prefix='ledgerlens-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_temporary.name) / 'test.db').replace('\\', '/')
os.environ['LOCAL_STORAGE_PATH'] = str(Path(_temporary.name) / 'objects')
os.environ['STORAGE_BACKEND'] = 'local'
os.environ['API_KEYS'] = '{"test-a":"org-a","test-b":"org-b"}'
os.environ['CLAMAV_HOST'] = ''

import pytest
from fastapi.testclient import TestClient
from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value
