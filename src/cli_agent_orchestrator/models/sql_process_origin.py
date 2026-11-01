"""Pure SQL origin wire vocabulary; a label is not authorization or absence proof."""

from uuid import UUID

RESOURCE_BINDING_FD_ENV = "CAO_INTERNAL_RESOURCE_BINDING_FD"
SQL_ORIGIN_PREFIX = "caos-sql-v1-"


def is_sql_process_application_name(value: object) -> bool:
    if type(value) is not str or not value.startswith(SQL_ORIGIN_PREFIX):
        return False
    suffix = value[len(SQL_ORIGIN_PREFIX) :]
    if len(suffix) != 32:
        return False
    try:
        identity = UUID(hex=suffix)
    except ValueError:
        return False
    return identity.version == 4 and identity.hex == suffix
