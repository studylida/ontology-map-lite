"""테스트 공통 픽스처 및 설정 (향후 신규 서비스 테스트 추가용)."""
import pytest

@pytest.fixture
def sample_fixture():
    return True
