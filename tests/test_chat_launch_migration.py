import aiosqlite

from app.db.migrations import MIGRATIONS, migrate


async def test_notice_cohort_is_fixed_when_migration_runs():
    async with aiosqlite.connect(":memory:") as db:
        for version, script in enumerate(MIGRATIONS[:22], 1):
            await db.executescript(script)
            await db.execute(f"PRAGMA user_version = {version}")
        await db.execute("INSERT INTO users (telegram_id) VALUES (1), (2)")
        await db.commit()
        await migrate(db)
        await db.execute("INSERT INTO users (telegram_id) VALUES (3)")
        await db.commit()
        await migrate(db)
        async with db.execute("SELECT telegram_id FROM chat_launch_notices ORDER BY telegram_id") as cursor:
            assert await cursor.fetchall() == [(1,), (2,)]


async def test_survey_cohort_is_existing_users_only():
    async with aiosqlite.connect(":memory:") as db:
        survey = next(i for i, script in enumerate(MIGRATIONS) if "app_survey" in script)
        for version, script in enumerate(MIGRATIONS[:survey], 1):
            await db.executescript(script)
            await db.execute(f"PRAGMA user_version = {version}")
        await db.execute("INSERT INTO users (telegram_id) VALUES (1), (2)")
        await db.commit()
        await migrate(db)
        await db.execute("INSERT INTO users (telegram_id) VALUES (3)")
        await db.commit()
        await migrate(db)
        async with db.execute("SELECT telegram_id FROM app_survey ORDER BY telegram_id") as cursor:
            assert await cursor.fetchall() == [(1,), (2,)]
