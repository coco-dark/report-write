import os

import pytest

os.environ["REPORT_WRITING_LLM"] = "scripted"
os.environ["REPORT_WRITING_DATABASE"] = "file"
os.environ["REPORT_WRITING_PARSE"] = "local"


@pytest.fixture(autouse=True)
def _reset_blueprint_memory_store():
    from report_writing.services.blueprint_catalog import clear_blueprint_memory_store

    clear_blueprint_memory_store()
    yield
    clear_blueprint_memory_store()
