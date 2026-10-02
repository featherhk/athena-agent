import pytest
from athena.config import Config
from athena.engine import Engine
from athena.store import Store


@pytest.fixture
def config(tmp_path):
    config = Config(data_dir=tmp_path / "state", repo_dir=tmp_path / "code", api_key="test-provider-key")
    config.prepare()
    return config


@pytest.fixture
async def store(config):
    store = Store(config.data_dir / "test.sqlite3")
    await store.initialize()
    yield store
    await store.close()


@pytest.fixture
async def engine(config, store):
    engine = Engine(config, store)
    yield engine
    await engine.close()
