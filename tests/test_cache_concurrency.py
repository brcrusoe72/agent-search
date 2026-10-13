import asyncio
import sqlite3
import pytest
from app.content_cache import ContentCache


@pytest.mark.parametrize('operation',['get','set'])
def test_locked_cache_does_not_block_other_coroutines(tmp_path, operation):
    cache=ContentCache(str(tmp_path/'cache.db'))
    blocker=sqlite3.connect(cache.db_path)
    blocker.execute('BEGIN EXCLUSIVE')
    async def run():
        call=cache.get('https://example.com') if operation=='get' else cache.set('https://example.com','text','direct')
        task=asyncio.create_task(call)
        try:
            await asyncio.sleep(0.02)
            assert not task.done(), 'SQLite wait blocked the event loop until its timeout'
        finally:
            blocker.rollback()
            await task
    try:
        asyncio.run(run())
    finally:
        blocker.close()
