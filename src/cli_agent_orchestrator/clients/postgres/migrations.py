"""Explicit native component migrations, private owner credentials and catalog proof."""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from psycopg import sql

from .config import DatabaseTarget
from .connection import Connection, connect


class SchemaMismatch(RuntimeError):
    """Installed PostgreSQL schema cannot safely serve this binary."""


@dataclass(frozen=True)
class ComponentMigration:
    component: str
    scope: str
    version: int
    statements: tuple[str, ...]
    tables: tuple[str, ...]

    @property
    def checksum(self):
        return hashlib.sha256(
            json.dumps(
                [self.component, self.scope, self.version, self.statements, self.tables],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _records(connection, query, parameters):
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def _canonical_constraint_definition_uncached(definition: str) -> str:
    """Normalize PostgreSQL's equivalent varchar-enum text-array representations.

    pg_dump reparses an array-wide varchar[] -> text[] coercion as individual
    varchar -> text element casts. Only unbounded varchar string literals in
    an explicitly text-typed array are normalized. Quoted SQL remains atomic;
    arbitrary expressions, bounded varchar casts, and other constraints retain
    their exact catalog definitions.
    """
    tokens = list(
        re.finditer(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|::|[A-Za-z_][A-Za-z_0-9]*|\S", definition)
    )
    values = [token.group().lower() for token in tokens]
    pieces: list[str] = []
    copied_until = 0
    index = 0
    while index < len(tokens):
        if values[index : index + 2] != ["array", "["]:
            index += 1
            continue
        cursor = index + 2
        literals: list[str] = []
        element_text_casts: list[bool] = []
        while cursor < len(tokens):
            literal = tokens[cursor].group()
            if not literal.startswith("'") or values[cursor + 1 : cursor + 4] != [
                "::",
                "character",
                "varying",
            ]:
                break
            literals.append(literal)
            cursor += 4
            text_cast = values[cursor : cursor + 2] == ["::", "text"]
            element_text_casts.append(text_cast)
            if text_cast:
                cursor += 2
            if cursor < len(tokens) and values[cursor] == ",":
                cursor += 1
                continue
            break
        if not literals or cursor >= len(tokens) or values[cursor] != "]":
            index += 1
            continue
        end = cursor + 1
        array_text_cast = values[end : end + 4] == ["::", "text", "[", "]"]
        if array_text_cast:
            end += 4
        elif not all(element_text_casts):
            index += 1
            continue
        pieces.append(definition[copied_until : tokens[index].start()])
        pieces.append("ARRAY[" + ", ".join(literal + "::text" for literal in literals) + "]")
        copied_until = tokens[end - 1].end()
        index = end
    pieces.append(definition[copied_until:])
    return "".join(pieces)


_CANONICAL_CONSTRAINT_CACHE_MAXSIZE = 512
_CANONICAL_CONSTRAINT_CACHE_MAX_CHARS = 4096


@lru_cache(maxsize=_CANONICAL_CONSTRAINT_CACHE_MAXSIZE)
def _canonical_constraint_definition_cache(definition: str) -> str:
    return _canonical_constraint_definition_uncached(definition)


def _canonical_constraint_definition(definition: str) -> str:
    """Cache only short, pure constraint-text normalization results."""
    if len(definition) > _CANONICAL_CONSTRAINT_CACHE_MAX_CHARS:
        return _canonical_constraint_definition_uncached(definition)
    return _canonical_constraint_definition_cache(definition)


def _catalog_manifest_queries() -> dict:
    return {
        "columns": """SELECT c.relname AS table_name, a.attname AS name, a.attnum AS position,
            pg_catalog.format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS required,
            a.attidentity AS identity, a.attgenerated AS generated,
            pg_get_expr(d.adbin,d.adrelid) AS default_value
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=%s AND c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname AS table_name, x.conname AS name,
            x.contype AS type, pg_get_constraintdef(x.oid,true) AS definition
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname,x.conname""",
        "indexes": """SELECT c.relname AS table_name, i.relname AS name,
            pg_get_indexdef(i.oid) AS definition FROM pg_index x
            JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname,i.relname""",
        "triggers": """SELECT c.relname AS table_name, t.tgname AS name,
            t.tgenabled AS activation,
            pg_get_triggerdef(t.oid,true) AS definition,
            p.proname AS function_name,
            (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                    ORDER BY argument.ordinal)
             FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS argument(type_oid,ordinal)
             JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
             JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
            ) AS function_identity_arguments,
            p.prokind AS function_kind
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_proc p ON p.oid=t.tgfoid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s) AND NOT t.tgisinternal
            ORDER BY c.relname,t.tgname""",
        "rules": """SELECT c.relname AS table_name, r.rulename AS name,
            pg_get_ruledef(r.oid,true) AS definition
            FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s)
            ORDER BY c.relname,r.rulename""",
        "sequences": """SELECT c.relname AS table_name, a.attname AS column_name,
            s.seqstart AS start, s.seqincrement AS increment, s.seqmin AS minimum,
            s.seqmax AS maximum, s.seqcache AS cache, s.seqcycle AS cycle
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            JOIN pg_depend d ON d.refobjid=c.oid AND d.refobjsubid=a.attnum AND d.deptype IN ('a','i')
            JOIN pg_sequence s ON s.seqrelid=d.objid
            WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname,a.attname""",
        "relations": """SELECT c.relname AS table_name, c.relkind AS kind,
            c.relpersistence AS persistence, c.relrowsecurity AS row_security_enabled,
            c.relforcerowsecurity AS row_security_forced
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s) ORDER BY c.relname""",
        "policies": """SELECT c.relname AS table_name, p.polname AS name,
            p.polcmd AS command, p.polpermissive AS permissive,
            ARRAY(SELECT CASE WHEN role_oid=0 THEN 'PUBLIC'
                              ELSE pg_catalog.pg_get_userbyid(role_oid) END
                  FROM unnest(p.polroles) AS allowed(role_oid)
                  ORDER BY 1) AS roles,
            pg_catalog.pg_get_expr(p.polqual,p.polrelid,true) AS using_expression,
            pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid,true) AS check_expression
            FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=ANY(%s)
            ORDER BY c.relname,p.polname""",
        "routines": """WITH target_namespace AS (
                SELECT oid FROM pg_namespace WHERE nspname=%s
            ), component_relations AS (
                SELECT c.oid FROM pg_class c
                JOIN target_namespace n ON n.oid=c.relnamespace
                WHERE c.relname=ANY(%s)
            ), referenced_routines AS (
                SELECT t.tgfoid AS routine_oid
                FROM pg_trigger t JOIN component_relations r ON r.oid=t.tgrelid
                WHERE NOT t.tgisinternal
                UNION
                SELECT d.refobjid AS routine_oid
                FROM pg_depend d
                WHERE d.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass
                  AND (
                    (d.classid='pg_catalog.pg_constraint'::pg_catalog.regclass
                     AND EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN component_relations r ON r.oid=c.conrelid
                        WHERE c.oid=d.objid
                     ))
                    OR (d.classid='pg_catalog.pg_attrdef'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_attrdef a
                            JOIN component_relations r ON r.oid=a.adrelid
                            WHERE a.oid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_class'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_index i
                            JOIN component_relations r ON r.oid=i.indrelid
                            WHERE i.indexrelid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_policy'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_policy p
                            JOIN component_relations r ON r.oid=p.polrelid
                            WHERE p.oid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_rewrite'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_rewrite r
                            JOIN component_relations t ON t.oid=r.ev_class
                            WHERE r.oid=d.objid
                        ))
                  )
            )
            SELECT p.proname AS name,
                (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                        ORDER BY argument.ordinal)
                 FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS argument(type_oid,ordinal)
                 JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
                 JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                ) AS identity_arguments,
                p.prokind AS kind,
                jsonb_build_object(
                    'language', language.lanname,
                    'source', p.prosrc,
                    'binary', p.probin,
                    'security_definer', p.prosecdef,
                    'leakproof', p.proleakproof,
                    'strict', p.proisstrict,
                    'returns_set', p.proretset,
                    'volatility', p.provolatile,
                    'parallel', p.proparallel,
                    'cost', p.procost,
                    'rows', p.prorows,
                    'argument_count', p.pronargs,
                    'argument_names', p.proargnames,
                    'argument_modes', p.proargmodes,
                    'argument_types',
                        (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                                ORDER BY argument.ordinal)
                         FROM unnest(COALESCE(p.proallargtypes,p.proargtypes::oid[]))
                              WITH ORDINALITY AS argument(type_oid,ordinal)
                         JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                        ),
                    'return_type', jsonb_build_array(return_namespace.nspname,return_type.typname),
                    'variadic_type', CASE WHEN p.provariadic=0 THEN NULL ELSE
                        (SELECT jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                         FROM pg_type type_catalog
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                         WHERE type_catalog.oid=p.provariadic) END,
                    'transform_types',
                        (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                                ORDER BY transform.ordinal)
                         FROM unnest(COALESCE(p.protrftypes,ARRAY[]::oid[]))
                              WITH ORDINALITY AS transform(type_oid,ordinal)
                         JOIN pg_type type_catalog ON type_catalog.oid=transform.type_oid
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                        ),
                    'configuration', p.proconfig
                ) AS definition,
                p.proargdefaults IS NOT NULL AS has_argument_defaults,
                p.prosqlbody IS NOT NULL AS has_sql_standard_body,
                p.prosupport<>0 AS has_support_routine
            FROM referenced_routines r
            JOIN pg_proc p ON p.oid=r.routine_oid
            JOIN target_namespace n ON n.oid=p.pronamespace
            JOIN pg_language language ON language.oid=p.prolang
            JOIN pg_type return_type ON return_type.oid=p.prorettype
            JOIN pg_namespace return_namespace ON return_namespace.oid=return_type.typnamespace
            ORDER BY p.proname,identity_arguments,p.prokind""",
    }


def _normalize_catalog_manifest(tables: tuple[str, ...], result: dict) -> dict:
    if any(
        row["kind"] == "a"
        or row["has_argument_defaults"]
        or row["has_sql_standard_body"]
        or row["has_support_routine"]
        for row in result["routines"]
    ):
        raise SchemaMismatch("PostgreSQL component references an unsupported routine definition")
    routine_definitions = {
        (
            row["name"],
            (
                "null"
                if row["identity_arguments"] is None
                else json.dumps(row["identity_arguments"], sort_keys=True, separators=(",", ":"))
            ),
            row["kind"],
        ): row["definition"]
        for row in result["routines"]
    }
    for trigger in result["triggers"]:
        function_name = trigger.pop("function_name")
        function_identity_arguments = trigger.pop("function_identity_arguments")
        function_kind = trigger.pop("function_kind")
        routine_key = (
            function_name,
            (
                "null"
                if function_identity_arguments is None
                else json.dumps(
                    function_identity_arguments,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            function_kind,
        )
        try:
            trigger["function"] = routine_definitions[routine_key]
        except KeyError as exc:
            raise SchemaMismatch("PostgreSQL trigger references an unsupported routine") from exc
    for row in result["routines"]:
        del row["has_argument_defaults"]
        del row["has_sql_standard_body"]
        del row["has_support_routine"]
    for constraint in result["constraints"]:
        if constraint["type"] == "c":
            constraint["definition"] = _canonical_constraint_definition(constraint["definition"])
    found = {row["table_name"] for row in result["columns"]}
    if found != set(tables):
        raise SchemaMismatch("PostgreSQL component tables are missing")
    return result


_CATALOG_MANIFEST_ORDER_COLUMNS = {
    "columns": ("table_name", "position"),
    "constraints": ("table_name", "name"),
    "indexes": ("table_name", "name"),
    "triggers": ("table_name", "name"),
    "rules": ("table_name", "name"),
    "sequences": ("table_name", "column_name"),
    "relations": ("table_name",),
    "policies": ("table_name", "name"),
    "routines": ("name", "identity_arguments", "kind"),
}


_CATALOG_MANIFEST_SHARED_QUERIES = MappingProxyType(
    {
        "columns": """SELECT c.relname AS table_name, a.attname AS name, a.attnum AS position,
            pg_catalog.format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS required,
            a.attidentity AS identity, a.attgenerated AS generated,
            pg_get_expr(d.adbin,d.adrelid) AS default_value
            FROM target_relations c
            JOIN pg_attribute a ON a.attrelid=c.oid
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE a.attnum>0 AND NOT a.attisdropped
            ORDER BY c.relname,a.attnum""",
        "constraints": """SELECT c.relname AS table_name, x.conname AS name,
            x.contype AS type, pg_get_constraintdef(x.oid,true) AS definition
            FROM pg_constraint x JOIN target_relations c ON c.oid=x.conrelid
            ORDER BY c.relname,x.conname""",
        "indexes": """SELECT c.relname AS table_name, i.relname AS name,
            pg_get_indexdef(i.oid) AS definition FROM pg_index x
            JOIN target_relations c ON c.oid=x.indrelid
            JOIN pg_class i ON i.oid=x.indexrelid
            ORDER BY c.relname,i.relname""",
        "triggers": """SELECT c.relname AS table_name, t.tgname AS name,
            t.tgenabled AS activation,
            pg_get_triggerdef(t.oid,true) AS definition,
            p.proname AS function_name,
            (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                    ORDER BY argument.ordinal)
             FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS argument(type_oid,ordinal)
             JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
             JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
            ) AS function_identity_arguments,
            p.prokind AS function_kind
            FROM pg_trigger t JOIN target_relations c ON c.oid=t.tgrelid
            JOIN pg_proc p ON p.oid=t.tgfoid
            WHERE NOT t.tgisinternal
            ORDER BY c.relname,t.tgname""",
        "rules": """SELECT c.relname AS table_name, r.rulename AS name,
            pg_get_ruledef(r.oid,true) AS definition
            FROM pg_rewrite r JOIN target_relations c ON c.oid=r.ev_class
            ORDER BY c.relname,r.rulename""",
        "sequences": """SELECT c.relname AS table_name, a.attname AS column_name,
            s.seqstart AS start, s.seqincrement AS increment, s.seqmin AS minimum,
            s.seqmax AS maximum, s.seqcache AS cache, s.seqcycle AS cycle
            FROM target_relations c
            JOIN pg_attribute a ON a.attrelid=c.oid
            JOIN pg_depend d ON d.refobjid=c.oid AND d.refobjsubid=a.attnum AND d.deptype IN ('a','i')
            JOIN pg_sequence s ON s.seqrelid=d.objid
            ORDER BY c.relname,a.attname""",
        "relations": """SELECT c.relname AS table_name, c.relkind AS kind,
            c.relpersistence AS persistence, c.relrowsecurity AS row_security_enabled,
            c.relforcerowsecurity AS row_security_forced
            FROM target_relations c ORDER BY c.relname""",
        "policies": """SELECT c.relname AS table_name, p.polname AS name,
            p.polcmd AS command, p.polpermissive AS permissive,
            ARRAY(SELECT CASE WHEN role_oid=0 THEN 'PUBLIC'
                              ELSE pg_catalog.pg_get_userbyid(role_oid) END
                  FROM unnest(p.polroles) AS allowed(role_oid)
                  ORDER BY 1) AS roles,
            pg_catalog.pg_get_expr(p.polqual,p.polrelid,true) AS using_expression,
            pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid,true) AS check_expression
            FROM pg_policy p JOIN target_relations c ON c.oid=p.polrelid
            ORDER BY c.relname,p.polname""",
        "routines": """WITH referenced_routines AS (
                SELECT t.tgfoid AS routine_oid
                FROM pg_trigger t JOIN target_relations r ON r.oid=t.tgrelid
                WHERE NOT t.tgisinternal
                UNION
                SELECT d.refobjid AS routine_oid
                FROM pg_depend d
                WHERE d.refclassid='pg_catalog.pg_proc'::pg_catalog.regclass
                  AND (
                    (d.classid='pg_catalog.pg_constraint'::pg_catalog.regclass
                     AND EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN target_relations r ON r.oid=c.conrelid
                        WHERE c.oid=d.objid
                     ))
                    OR (d.classid='pg_catalog.pg_attrdef'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_attrdef a
                            JOIN target_relations r ON r.oid=a.adrelid
                            WHERE a.oid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_class'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_index i
                            JOIN target_relations r ON r.oid=i.indrelid
                            WHERE i.indexrelid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_policy'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_policy p
                            JOIN target_relations r ON r.oid=p.polrelid
                            WHERE p.oid=d.objid
                        ))
                    OR (d.classid='pg_catalog.pg_rewrite'::pg_catalog.regclass
                        AND EXISTS (
                            SELECT 1 FROM pg_rewrite r
                            JOIN target_relations t ON t.oid=r.ev_class
                            WHERE r.oid=d.objid
                        ))
                  )
            )
            SELECT p.proname AS name,
                (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                        ORDER BY argument.ordinal)
                 FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS argument(type_oid,ordinal)
                 JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
                 JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                ) AS identity_arguments,
                p.prokind AS kind,
                jsonb_build_object(
                    'language', language.lanname,
                    'source', p.prosrc,
                    'binary', p.probin,
                    'security_definer', p.prosecdef,
                    'leakproof', p.proleakproof,
                    'strict', p.proisstrict,
                    'returns_set', p.proretset,
                    'volatility', p.provolatile,
                    'parallel', p.proparallel,
                    'cost', p.procost,
                    'rows', p.prorows,
                    'argument_count', p.pronargs,
                    'argument_names', p.proargnames,
                    'argument_modes', p.proargmodes,
                    'argument_types',
                        (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                                ORDER BY argument.ordinal)
                         FROM unnest(COALESCE(p.proallargtypes,p.proargtypes::oid[]))
                              WITH ORDINALITY AS argument(type_oid,ordinal)
                         JOIN pg_type type_catalog ON type_catalog.oid=argument.type_oid
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                        ),
                    'return_type', jsonb_build_array(return_namespace.nspname,return_type.typname),
                    'variadic_type', CASE WHEN p.provariadic=0 THEN NULL ELSE
                        (SELECT jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                         FROM pg_type type_catalog
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                         WHERE type_catalog.oid=p.provariadic) END,
                    'transform_types',
                        (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                                ORDER BY transform.ordinal)
                         FROM unnest(COALESCE(p.protrftypes,ARRAY[]::oid[]))
                              WITH ORDINALITY AS transform(type_oid,ordinal)
                         JOIN pg_type type_catalog ON type_catalog.oid=transform.type_oid
                         JOIN pg_namespace type_namespace ON type_namespace.oid=type_catalog.typnamespace
                        ),
                    'configuration', p.proconfig
                ) AS definition,
                p.proargdefaults IS NOT NULL AS has_argument_defaults,
                p.prosqlbody IS NOT NULL AS has_sql_standard_body,
                p.prosupport<>0 AS has_support_routine
            FROM referenced_routines r
            JOIN pg_proc p ON p.oid=r.routine_oid
            JOIN target_namespace n ON n.oid=p.pronamespace
            JOIN pg_language language ON language.oid=p.prolang
            JOIN pg_type return_type ON return_type.oid=p.prorettype
            JOIN pg_namespace return_namespace ON return_namespace.oid=return_type.typnamespace
            ORDER BY p.proname,identity_arguments,p.prokind""",
    }
)


def _build_shared_catalog_manifest_statement():
    entries = []
    for kind, query in _CATALOG_MANIFEST_SHARED_QUERIES.items():
        order_by = sql.SQL(", ").join(
            sql.Identifier("manifest_row", column)
            for column in _CATALOG_MANIFEST_ORDER_COLUMNS[kind]
        )
        entries.append(
            sql.SQL(
                "%s::text, COALESCE((SELECT json_agg(row_to_json(manifest_row) ORDER BY {}) "
                "FROM ({}) AS manifest_row), '[]'::json)"
            ).format(order_by, sql.SQL(query))
        )
    return sql.SQL(
        "WITH target_namespace AS MATERIALIZED ("
        " SELECT oid FROM pg_namespace WHERE nspname=%s"
        "), target_relations AS MATERIALIZED ("
        " SELECT c.oid, c.relname, c.relkind, c.relpersistence,"
        " c.relrowsecurity, c.relforcerowsecurity"
        " FROM pg_class c JOIN target_namespace n ON n.oid=c.relnamespace"
        " WHERE c.relname=ANY(%s)"
        ") SELECT json_build_object({}) AS manifest"
    ).format(sql.SQL(", ").join(entries))


_CATALOG_MANIFEST_SHARED_STATEMENT = _build_shared_catalog_manifest_statement()


_CATALOG_MANIFEST_LITERAL_CACHE_MAX_TABLES = 256
_CATALOG_MANIFEST_LITERAL_CACHE_MAX_CHARS = 65536


def _build_catalog_manifest_literal_statement(schema: str, tables: tuple[str, ...]):
    table_array = sql.SQL("ARRAY[{}]::text[]").format(
        sql.SQL(", ").join(sql.Literal(name) for name in tables)
    )
    entries = []
    for kind, query in _CATALOG_MANIFEST_SHARED_QUERIES.items():
        order_by = sql.SQL(", ").join(
            sql.Identifier("manifest_row", column)
            for column in _CATALOG_MANIFEST_ORDER_COLUMNS[kind]
        )
        entries.append(
            sql.SQL(
                "{}::text, COALESCE((SELECT json_agg(row_to_json(manifest_row) ORDER BY {}) "
                "FROM ({}) AS manifest_row), '[]'::json)"
            ).format(sql.Literal(kind), order_by, sql.SQL(query))
        )
    composed = sql.SQL(
        "WITH target_namespace AS MATERIALIZED ("
        " SELECT oid FROM pg_namespace WHERE nspname={}::text"
        "), target_relations AS MATERIALIZED ("
        " SELECT c.oid, c.relname, c.relkind, c.relpersistence,"
        " c.relrowsecurity, c.relforcerowsecurity"
        " FROM pg_class c JOIN target_namespace n ON n.oid=c.relnamespace"
        " WHERE c.relname=ANY({})"
        ") SELECT json_build_object({}) AS manifest"
    ).format(
        sql.Literal(schema),
        table_array,
        sql.SQL(", ").join(entries),
    )
    # Flatten at the bounded cache miss; executions then pass plain SQL through
    # the regular native or lease-guarded Connection.execute path.
    return sql.SQL(composed.as_string(None))


@lru_cache(maxsize=128)
def _cached_catalog_manifest_literal_statement(schema: str, tables: tuple[str, ...]):
    return _build_catalog_manifest_literal_statement(schema, tables)


def _catalog_manifest_literal_statement(schema: str, tables: tuple[str, ...]):
    table_names = tuple(tables)
    cacheable = (
        type(schema) is str
        and type(tables) is tuple
        and all(type(name) is str for name in table_names)
        and len(table_names) <= _CATALOG_MANIFEST_LITERAL_CACHE_MAX_TABLES
        and len(schema) + sum(len(name) for name in table_names)
        <= _CATALOG_MANIFEST_LITERAL_CACHE_MAX_CHARS
    )
    if not cacheable:
        return _build_catalog_manifest_literal_statement(schema, table_names)
    return _cached_catalog_manifest_literal_statement(schema, table_names)


def catalog_manifest(connection: Connection, tables: tuple[str, ...]) -> dict:
    """Fingerprint structural definitions in one fresh catalog statement."""
    family_order = tuple(_CATALOG_MANIFEST_SHARED_QUERIES)
    row = connection.execute(
        _catalog_manifest_literal_statement(connection.target.schema, tables),
        prepare=True,
    ).fetchone()
    if row is None:
        raise SchemaMismatch("PostgreSQL catalog query returned no manifest")
    raw_manifest = row[0]
    ordered_manifest = {kind: raw_manifest[kind] for kind in family_order}
    return _normalize_catalog_manifest(tables, ordered_manifest)


def _digest(manifest):
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _catalog_manifest_digest(manifest):
    """Digest a fresh catalog manifest, which contains only JSON-safe acyclic values."""
    return hashlib.sha256(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":"),
            check_circular=False,
        ).encode()
    ).hexdigest()


_RUNTIME_ROLE_QUERY = """WITH role_flags AS MATERIALIZED (
            SELECT r.rolsuper,r.rolbypassrls,r.rolcreaterole,r.rolcreatedb,r.rolreplication
            FROM pg_roles r WHERE r.rolname=current_user
        ), role_safety AS MATERIALIZED (
            SELECT count(*)>0 AS role_exists,
                COALESCE(bool_or(
                    rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication
                ), true) AS unsafe
            FROM role_flags
        ), namespace_acl AS MATERIALIZED (
            SELECT CASE WHEN role_safety.role_exists AND NOT role_safety.unsafe THEN EXISTS(
                SELECT 1 FROM pg_namespace n,
                LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                WHERE n.nspname=%s AND a.privilege_type='CREATE'
                  AND a.grantee<>n.nspowner LIMIT 1
            ) ELSE false END AS untrusted_create
            FROM role_safety
        ), object_ownership AS MATERIALIZED (
            SELECT CASE WHEN role_safety.role_exists AND NOT role_safety.unsafe
                              AND NOT namespace_acl.untrusted_create THEN EXISTS(
                SELECT 1 FROM (
                    SELECT c.relowner AS owner FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s
                    UNION ALL
                    SELECT p.proowner AS owner FROM pg_proc p
                    JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s
                ) AS schema_object_owners
                WHERE pg_has_role(current_user,owner,'MEMBER') LIMIT 1
            ) ELSE false END AS owns_objects
            FROM role_safety CROSS JOIN namespace_acl
        ), namespace_privilege AS MATERIALIZED (
            SELECT CASE WHEN role_safety.role_exists AND NOT role_safety.unsafe
                              AND NOT namespace_acl.untrusted_create
                              AND NOT object_ownership.owns_objects
                THEN (SELECT has_schema_privilege(current_user,n.oid,'CREATE')
                      FROM pg_namespace n WHERE n.nspname=%s)
                ELSE NULL END AS can_create
            FROM role_safety CROSS JOIN namespace_acl CROSS JOIN object_ownership
        ), protected_table_acl AS MATERIALIZED (
            SELECT CASE WHEN role_safety.role_exists AND NOT role_safety.unsafe
                              AND NOT namespace_acl.untrusted_create
                              AND NOT object_ownership.owns_objects
                              AND namespace_privilege.can_create IS FALSE
                THEN EXISTS(
                    SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname=%s
                      AND c.relname IN (
                          '_cao_schema_migrations',
                          '_cao_store_identity',
                          '_cao_import_receipts'
                      )
                      AND c.relkind IN ('r','p')
                      AND (
                          NOT has_table_privilege(current_user,c.oid,'SELECT')
                          OR has_any_column_privilege(current_user,c.oid,'INSERT')
                          OR has_any_column_privilege(current_user,c.oid,'UPDATE')
                          OR has_table_privilege(current_user,c.oid,'DELETE')
                          OR has_table_privilege(current_user,c.oid,'TRUNCATE')
                          OR has_any_column_privilege(current_user,c.oid,'REFERENCES')
                          OR has_table_privilege(current_user,c.oid,'TRIGGER')
                          OR has_table_privilege(current_user,c.oid,'MAINTAIN')
                      )
                    LIMIT 1
                )
                ELSE false END AS drift
            FROM role_safety CROSS JOIN namespace_acl CROSS JOIN object_ownership
            CROSS JOIN namespace_privilege
        )
        SELECT role_safety.role_exists,role_safety.unsafe,
            namespace_acl.untrusted_create,object_ownership.owns_objects,
            namespace_privilege.can_create,protected_table_acl.drift
        FROM role_safety CROSS JOIN namespace_acl CROSS JOIN object_ownership
        CROSS JOIN namespace_privilege CROSS JOIN protected_table_acl"""

_RUNTIME_ROLE_SQL_CACHE_MAX_SCHEMA_CHARS = 4096


def _build_runtime_role_statement(schema: str) -> str:
    parts = _RUNTIME_ROLE_QUERY.split("%s")
    if len(parts) != 6:
        raise RuntimeError("runtime role query must contain five schema bind positions")
    rendered_schema = sql.Literal(schema).as_string(None)
    return "".join(
        part + (rendered_schema if index < 5 else "") for index, part in enumerate(parts)
    )


@lru_cache(maxsize=128)
def _cached_runtime_role_statement(schema: str) -> str:
    """Cache only the safely rendered role SQL; permission data stays live."""
    return _build_runtime_role_statement(schema)


def _runtime_role_statement(schema: object) -> str | None:
    if type(schema) is not str or len(schema) > _RUNTIME_ROLE_SQL_CACHE_MAX_SCHEMA_CHARS:
        return None
    return _cached_runtime_role_statement(schema)


def verify_runtime_role(connection: Connection):
    schema = connection.target.schema
    statement = _runtime_role_statement(schema)
    if statement is None:
        row = connection.execute(_RUNTIME_ROLE_QUERY, (schema,) * 5).fetchone()
    else:
        row = connection.execute(statement, prepare=True).fetchone()
    if row is None or not row[0] or row[1]:
        raise SchemaMismatch("PostgreSQL runtime role has unsafe privileges")
    if row[2]:
        raise SchemaMismatch("PostgreSQL namespace allows untrusted DDL")
    if row[3]:
        raise SchemaMismatch("PostgreSQL runtime role must not own schema objects")
    if row[4] is None:
        raise SchemaMismatch("PostgreSQL target namespace is missing")
    if row[4]:
        raise SchemaMismatch("PostgreSQL runtime role must not create schema objects")
    if row[5]:
        raise SchemaMismatch(
            "PostgreSQL runtime role must have read-only access to protected registry tables"
        )


def verify_component(connection: Connection, migrations):
    migrations = tuple(migrations)
    if not migrations:
        raise ValueError("A component migration is required")
    scope, component = migrations[0].scope, migrations[0].component
    if connection.target.scope != scope:
        raise SchemaMismatch("PostgreSQL component scope mismatch")
    identity = connection.execute(
        "SELECT scope,store_id FROM _cao_store_identity WHERE singleton=1"
    ).fetchone()
    if identity is None or identity[0] != scope or identity[1] != connection.target.store_id:
        raise SchemaMismatch("PostgreSQL logical store identity mismatch")
    records = connection.execute(
        """SELECT version,checksum,manifest_digest FROM _cao_schema_migrations
        WHERE scope=%s AND component=%s ORDER BY version""",
        (scope, component),
    ).fetchall()
    if len(records) != len(migrations) or any(
        row["version"] != migration.version or row["checksum"] != migration.checksum
        for row, migration in zip(records, migrations)
    ):
        raise SchemaMismatch("PostgreSQL component migration history mismatch")
    tables = tuple(sorted({name for migration in migrations for name in migration.tables}))
    if records[-1]["manifest_digest"] != _catalog_manifest_digest(
        catalog_manifest(connection, tables)
    ):
        raise SchemaMismatch("PostgreSQL component schema fingerprint mismatch")


def migrate_component(target: DatabaseTarget, migrations):
    """Controlled bootstrap only. Ordinary runtime callers only verify."""
    migrations = tuple(migrations)
    if not migrations or any(
        m.scope != target.scope or m.component != migrations[0].component for m in migrations
    ):
        raise ValueError("Invalid migration component")
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("Migration versions must be contiguous")
    with connect(target.migration_target()) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"cao-schema:{target.database}:{target.schema}",),
            )
            connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(target.schema))
            )
            owner = connection.execute(
                "SELECT n.nspowner=r.oid FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user WHERE n.nspname=%s",
                (target.schema,),
            ).fetchone()
            if owner is None or not owner[0]:
                raise SchemaMismatch("PostgreSQL namespace belongs to another migration owner")
            untrusted_create = connection.execute(
                """SELECT 1 FROM pg_namespace n,
                LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
                WHERE n.nspname=%s AND a.privilege_type='CREATE' AND a.grantee<>n.nspowner LIMIT 1""",
                (target.schema,),
            ).fetchone()
            if untrusted_create is not None:
                raise SchemaMismatch("PostgreSQL namespace allows untrusted DDL")
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS _cao_schema_migrations (
                scope TEXT NOT NULL, component TEXT NOT NULL, version INTEGER NOT NULL,
                checksum TEXT NOT NULL, manifest_digest TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(scope,component,version))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS _cao_store_identity (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL,
                store_id TEXT NOT NULL UNIQUE)""")
            connection.execute(
                "INSERT INTO _cao_store_identity(singleton,scope,store_id) VALUES (1,%s,%s) ON CONFLICT(singleton) DO NOTHING",
                (target.scope, target.store_id),
            )
            identity = connection.execute(
                "SELECT scope,store_id FROM _cao_store_identity WHERE singleton=1"
            ).fetchone()
            if identity is None or identity[0] != target.scope or identity[1] != target.store_id:
                raise SchemaMismatch("PostgreSQL logical store identity mismatch")
            installed = connection.execute(
                "SELECT version,checksum FROM _cao_schema_migrations WHERE scope=%s AND component=%s ORDER BY version",
                (target.scope, migrations[0].component),
            ).fetchall()
            if len(installed) > len(migrations) or any(
                row[0] != migration.version or row[1] != migration.checksum
                for row, migration in zip(installed, migrations)
            ):
                raise SchemaMismatch("Unknown or modified PostgreSQL migration")
            if installed:
                verify_component(connection, migrations[: len(installed)])
            for index, migration in enumerate(migrations[len(installed) :], start=len(installed)):
                for statement in migration.statements:
                    connection.execute(statement)
                tables = tuple(sorted({name for m in migrations[: index + 1] for name in m.tables}))
                connection.execute(
                    "INSERT INTO _cao_schema_migrations(scope,component,version,checksum,manifest_digest) VALUES (%s,%s,%s,%s,%s)",
                    (
                        migration.scope,
                        migration.component,
                        migration.version,
                        migration.checksum,
                        _catalog_manifest_digest(catalog_manifest(connection, tables)),
                    ),
                )
            connection.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(target.schema))
            )
            connection.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(target.schema), sql.Identifier(target.user))
            )
            connection.execute(
                sql.SQL(
                    "REVOKE INSERT,UPDATE,DELETE ON _cao_schema_migrations, _cao_store_identity FROM {}"
                ).format(sql.Identifier(target.user))
            )
            connection.execute(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            receipt = connection.execute(
                """SELECT c.oid,c.relkind FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname='_cao_import_receipts'""",
                (target.schema,),
            ).fetchone()
            if receipt is not None:
                if receipt[1] not in {"r", "p"}:
                    raise SchemaMismatch("PostgreSQL import receipt relation is not a table")
                receipt_table = sql.Identifier(target.schema, "_cao_import_receipts")
                runtime_role = sql.Identifier(target.user)
                connection.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM PUBLIC").format(receipt_table)
                )
                connection.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {}").format(
                        receipt_table, runtime_role
                    )
                )
                connection.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(receipt_table, runtime_role)
                )
                privileges = (
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                    "MAINTAIN",
                )
                checks = ",".join(
                    f"has_table_privilege(%s,%s,'{privilege}')" for privilege in privileges
                )
                parameters = tuple(value for _ in privileges for value in (target.user, receipt[0]))
                effective = connection.execute(f"SELECT {checks}", parameters).fetchone()
                if effective is None or not effective[0] or any(effective[1:]):
                    raise SchemaMismatch(
                        "PostgreSQL runtime role must have read-only import receipts"
                    )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
