import pytest

from app import create_app
from app.config import Config


@pytest.fixture
def app(tmp_path):
    class KonfigUji(Config):
        TESTING = True
        START_SERVICES = False          # jangan buka serial/kamera saat uji
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "uji.db").as_posix()
        UPLOAD_DIR = tmp_path / "uploads"
        SERIAL_MODE = "mock"
        CAMERA_MODE = "mock"
        # Uji perilaku lama (fail-safe) secara eksplisit; mode kunci pegas
        # diuji terpisah dengan override di dalam test.
        KUNCI_FAIL_SECURE = False
        UNLOCK_MS = 3000

    aplikasi = create_app(KonfigUji)
    yield aplikasi


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield
