"""Private content catalog for immutable static executable bytes."""

EXECUTABLE_CONTENT_SCHEMA = (
    """CREATE TABLE work_executable_contents (
        content_hash TEXT PRIMARY KEY NOT NULL
            CHECK(typeof(content_hash)='text' AND length(content_hash)=64
                AND content_hash NOT GLOB '*[^0-9a-f]*'),
        immutable_location TEXT NOT NULL
            CHECK(typeof(immutable_location)='text' AND immutable_location=content_hash),
        byte_length INTEGER NOT NULL
            CHECK(typeof(byte_length)='integer' AND byte_length BETWEEN 1 AND 8388608),
        created_at REAL NOT NULL CHECK(typeof(created_at)='real')
    )""",
    """CREATE TRIGGER work_executable_contents_immutable_update
        BEFORE UPDATE ON work_executable_contents
        BEGIN SELECT RAISE(ABORT,'executable content catalog is immutable'); END""",
    """CREATE TRIGGER work_executable_contents_immutable_delete
        BEFORE DELETE ON work_executable_contents
        BEGIN SELECT RAISE(ABORT,'executable content catalog cannot be deleted'); END""",
)
