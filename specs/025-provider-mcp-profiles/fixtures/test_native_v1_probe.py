"""Synthetic native-v1 SQLite contracts; never open operator/native state."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import native_v1_probe as probe

NONCE = "CAO-TURN-RECEIPT-" + "a" * 32
SECRET = "synthetic-private-content-never-export"


class NativeTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "opencode.db"
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""CREATE TABLE message(id TEXT,session_id TEXT,data TEXT);
          CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,data TEXT);
          CREATE TABLE credential(secret TEXT);""")
        self.db.execute("INSERT INTO credential VALUES(?)", (SECRET,))
        self.message("u", "user")
        self.part("u", "prompt", NONCE)
        self.message("a", "assistant", parentID="u", time={"completed": 1})
        self.part("a", "text", probe.MARKER + "\n" + NONCE)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def message(self, mid, role, **extra):
        self.db.execute(
            "INSERT INTO message VALUES(?,?,?)",
            (mid, "session", json.dumps(dict(role=role, **extra))),
        )

    def part(self, mid, pid, text, kind="text", session="session"):
        self.db.execute(
            "INSERT INTO part VALUES(?,?,?,?)",
            (pid, mid, session, json.dumps(dict(type=kind, text=text))),
        )

    def read(self):
        self.db.commit()
        return probe._native_parts(self.path, NONCE, time.monotonic() + 2)

    def test_legacy_schema_and_typed_parts(self):
        self.part("a", "reason", SECRET, "reasoning")
        self.part("a", "tool", SECRET, "tool")
        self.assertEqual(self.read(), [probe.MARKER + "\n" + NONCE])

    def test_duplicate_user_rejected(self):
        self.message("u2", "user")
        self.part("u2", "p2", NONCE)
        with self.assertRaisesRegex(probe.ProbeError, "user_not_unique"):
            self.read()

    def test_wrong_parent_and_cross_session_rejected(self):
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (json.dumps(dict(role="assistant", parentID="other", time={"completed": 1})),),
        )
        with self.assertRaisesRegex(probe.ProbeError, "assistant_receipt_not_unique"):
            self.read()

    def test_incomplete_rejected(self):
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (json.dumps(dict(role="assistant", parentID="u")),),
        )
        with self.assertRaisesRegex(probe.ProbeError, "assistant_receipt_not_unique"):
            self.read()

    def test_size_before_text_fetch(self):
        self.db.execute(
            "UPDATE part SET data=? WHERE id='text'",
            (json.dumps(dict(type="text", text="x" * (probe.CAP + 1))),),
        )
        with self.assertRaisesRegex(probe.ProbeError, "text_size_or_type"):
            self.read()

    def test_total_and_parts_bound(self):
        for i in range(17):
            self.part("a", f"extra{i}", "x")
        with self.assertRaisesRegex(probe.ProbeError, "parts_limit"):
            self.read()

    def test_readonly_and_credential_denied(self):
        self.db.set_authorizer(probe._authorizer)
        for query in (
            "SELECT secret FROM credential",
            "DELETE FROM part",
            "ATTACH DATABASE ':memory:' AS extra",
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                self.db.execute(query)

    def test_deadline(self):
        with self.assertRaisesRegex(probe.ProbeError, "deadline"):
            probe._native_parts(self.path, NONCE, time.monotonic() - 1)

    def test_duplicate_assistant_rejected(self):
        self.message("a2", "assistant", parentID="u", time={"completed": 2})
        self.part("a2", "text2", probe.MARKER + "\n" + NONCE)
        with self.assertRaisesRegex(probe.ProbeError, "assistant_receipt_not_unique"):
            self.read()

    def test_cross_session_part_rejected(self):
        self.db.execute("UPDATE part SET session_id='other' WHERE id='text'")
        with self.assertRaisesRegex(probe.ProbeError, "assistant_receipt_not_unique"):
            self.read()

    def test_utf8_byte_limit(self):
        self.db.execute(
            "UPDATE part SET data=? WHERE id='text'",
            (json.dumps(dict(type="text", text="中" * (probe.CAP // 3 + 1))),),
        )
        with self.assertRaisesRegex(probe.ProbeError, "text_size_or_type"):
            self.read()

    def test_total_limit_before_fetch(self):
        self.part("a", "large1", "x" * 40000)
        self.part("a", "large2", "x" * 40000)
        with self.assertRaisesRegex(probe.ProbeError, "total_size"):
            self.read()

    def test_schema_view_rejected(self):
        self.db.executescript(
            "ALTER TABLE message RENAME TO source; CREATE VIEW message AS SELECT * FROM source;"
        )
        with self.assertRaisesRegex(probe.ProbeError, "native_schema_mismatch"):
            self.read()

    def test_receipt_only_in_reasoning_rejected(self):
        self.db.execute(
            "UPDATE part SET data=? WHERE id='text'",
            (json.dumps(dict(type="reasoning", text=NONCE)),),
        )
        with self.assertRaisesRegex(probe.ProbeError, "assistant_receipt_not_unique"):
            self.read()

    def test_bound_observation_privacy_and_owned_major(self):
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        root = Path(self.temp.name)
        os.chmod(root, 0o700)
        home = root / "home/.aws/cli-agent-orchestrator"
        home.mkdir(parents=True, mode=0o700)
        owned = PrivateConfigOwnership(home, "sender").allocate(1)
        dest = owned / "data/opencode"
        dest.mkdir(parents=True)
        self.db.commit()
        private = dest / "opencode.db"
        backup = sqlite3.connect(private)
        self.db.backup(backup)
        backup.close()
        with patch.object(
            probe, "_digest", return_value=probe.hashlib.sha256(NONCE.encode()).hexdigest()
        ):
            result = probe.observe(
                root, home, "sender", None, NONCE, "other output", time.monotonic() + 3
            )
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["classification"], "native_marker_api_missing")
        self.assertFalse(result["native_equals_api"])
        self.assertTrue(result["active_receipt_bound"])
        for forbidden in (NONCE, SECRET, str(root), probe.MARKER):
            self.assertNotIn(forbidden, json.dumps(result))
        renamed = private.with_suffix(".original")
        private.rename(renamed)
        private.symlink_to(renamed)
        with self.assertRaisesRegex(probe.ProbeError, "path_owner_or_link"):
            probe._owned_db(root, home, "sender")

    def test_missing_active_digest_fail_closed(self):
        with patch.object(probe, "_digest", return_value="0" * 64):
            result = probe.observe(
                Path(self.temp.name),
                Path(self.temp.name),
                "sender",
                None,
                NONCE,
                "ACK",
                time.monotonic() + 3,
            )
        self.assertEqual(result["reason"], "active_nonce_not_unique")
        self.assertEqual(result["status"], "unavailable")

    def test_read_does_not_mutate_database(self):
        self.db.commit()
        before = self.path.read_bytes()
        self.read()
        self.assertEqual(before, self.path.read_bytes())

    def test_normalization_observational_only(self):
        value = "Validado n\u0303, 中文."
        shape = probe.shape(value)
        self.assertTrue(shape["nfc_marker"])
        self.assertFalse(shape["contains_marker"])
        self.assertFalse(shape["exact_marker"])

    def test_pg_requires_verified_receipt_at_completed_hook(self):
        from types import SimpleNamespace

        import psycopg

        target = SimpleNamespace(schema="own", connection_parameters=lambda: {})
        for phase in ("prepared", "sent", "result_verified"):
            with patch.object(psycopg, "connect") as connect:
                connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                    "a" * 64,
                    phase,
                )
                if phase == "result_verified":
                    self.assertEqual(
                        probe._digest(target, "sender", time.monotonic() + 3), "a" * 64
                    )
                else:
                    with self.assertRaisesRegex(
                        probe.ProbeError, "pg_receipt_missing_or_incomplete"
                    ):
                        probe._digest(target, "sender", time.monotonic() + 3)

    def test_ack_expected_marker_does_not_change_unicode_default(self):
        self.assertTrue(probe.shape("ACK", "ACK")["exact_marker"])
        self.assertFalse(probe.shape("ACK")["exact_marker"])
        self.assertTrue(probe.shape(probe.MARKER)["exact_marker"])

    def test_ack_native_api_equivalence_and_divergence(self):
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (
                json.dumps(
                    dict(role="assistant", parentID="u", time={"completed": 1}, agent="v1-receiver")
                ),
            ),
        )
        for native, api, equal, exact in (
            ("ACK", "ACK", True, True),
            ("ACK", "other", False, True),
            ("other", "other", True, False),
        ):
            self.db.execute(
                "UPDATE part SET data=? WHERE id='text'",
                (json.dumps(dict(type="text", text=native + "\n" + NONCE)),),
            )
            self.db.commit()
            st = self.path.stat()
            with (
                patch.object(
                    probe, "_digest", return_value=probe.hashlib.sha256(NONCE.encode()).hexdigest()
                ),
                patch.object(probe, "_owned_db", return_value=(self.path, (st.st_dev, st.st_ino))),
            ):
                result = probe.observe(
                    Path(self.temp.name),
                    Path(self.temp.name),
                    "receiver",
                    None,
                    NONCE,
                    api,
                    time.monotonic() + 3,
                    expected="ACK",
                    expected_agent="v1-receiver",
                )
            self.assertEqual(result["status"], "observed")
            self.assertIs(result["native_agent_matches"], True)
            self.assertEqual(result["native_equals_api"], equal)
            self.assertEqual(result["native_public"]["exact_marker"], exact)
            self.assertNotIn(NONCE, json.dumps(result))

    def test_arbitrary_expected_marker_rejected(self):
        result = probe.observe(
            Path(self.temp.name),
            Path(self.temp.name),
            "receiver",
            None,
            NONCE,
            "ACK",
            time.monotonic() + 3,
            expected=SECRET,
        )
        self.assertEqual(result["reason"], "expected_marker_invalid")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_non_utf8_database_rejected_before_row_measurement(self):
        alternate = self.path.with_name("utf16.db")
        with closing(sqlite3.connect(alternate)) as other:
            other.execute("PRAGMA encoding='UTF-16'")
            other.executescript("\n".join(self.db.iterdump()))
            self.assertNotEqual(other.execute("PRAGMA encoding").fetchone()[0], "UTF-8")
        with self.assertRaisesRegex(probe.ProbeError, "native_schema_mismatch"):
            probe._native_parts(alternate, NONCE, time.monotonic() + 2)

    def test_multibyte_json_row_byte_cap(self):
        for table, identity, payload, error in (
            ("message", "u", dict(role="user"), "user_not_unique"),
            (
                "message",
                "a",
                dict(role="assistant", parentID="u", time={"completed": 1}, agent="v1-receiver"),
                "assistant_receipt_not_unique",
            ),
            (
                "part",
                "text",
                dict(type="text", text=probe.MARKER + "\n" + NONCE),
                "assistant_receipt_not_unique",
            ),
        ):
            with self.subTest(table=table, identity=identity):
                old = self.db.execute(
                    f"SELECT data FROM {table} WHERE id=?", (identity,)
                ).fetchone()[0]
                encoded = json.dumps(dict(**payload, padding="界" * 350000), ensure_ascii=False)
                self.assertLess(len(encoded), 1048576)
                self.assertGreater(len(encoded.encode("utf-8")), 1048576)
                self.db.execute(f"UPDATE {table} SET data=? WHERE id=?", (encoded, identity))
                self.db.commit()
                try:
                    with self.assertRaisesRegex(probe.ProbeError, error):
                        probe._native_parts(self.path, NONCE, time.monotonic() + 2)
                finally:
                    self.db.execute(f"UPDATE {table} SET data=? WHERE id=?", (old, identity))
                    self.db.commit()

    def test_native_agent_metadata_byte_boundaries(self):
        for actual, expected, error in (
            ("x" * 64, "x" * 64, None),
            ("x" * 65, "x" * 64, "native_agent_metadata_invalid"),
            ("界" * 22, "v1-receiver", "native_agent_metadata_invalid"),
        ):
            with self.subTest(agent_bytes=len(actual.encode("utf-8"))):
                self.db.execute(
                    "UPDATE message SET data=? WHERE id='a'",
                    (
                        json.dumps(
                            dict(
                                role="assistant", parentID="u", time={"completed": 1}, agent=actual
                            ),
                            ensure_ascii=False,
                        ),
                    ),
                )
                self.db.commit()
                if error:
                    with self.assertRaisesRegex(probe.ProbeError, error):
                        probe._native_parts(
                            self.path, NONCE, time.monotonic() + 2, expected_agent=expected
                        )
                else:
                    self.assertEqual(
                        probe._native_parts(
                            self.path, NONCE, time.monotonic() + 2, expected_agent=expected
                        ),
                        [probe.MARKER + "\n" + NONCE],
                    )

    def test_observed_agent_mismatch_exports_no_private_values(self):
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (
                json.dumps(
                    dict(role="assistant", parentID="u", time={"completed": 1}, agent=SECRET)
                ),
            ),
        )
        self.db.commit()
        identity = self.path.stat()
        with (
            patch.object(
                probe, "_digest", return_value=probe.hashlib.sha256(NONCE.encode()).hexdigest()
            ),
            patch.object(
                probe, "_owned_db", return_value=(self.path, (identity.st_dev, identity.st_ino))
            ),
        ):
            result = probe.observe(
                Path(self.temp.name),
                Path(self.temp.name),
                "receiver",
                None,
                NONCE,
                SECRET,
                time.monotonic() + 3,
                expected="ACK",
                expected_agent="v1-receiver",
            )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "native_agent_mismatch")
        self.assertIs(result["native_agent_matches"], False)
        for private in (SECRET, NONCE, probe.MARKER):
            self.assertNotIn(private, json.dumps(result))

    def test_native_agent_identity_bound_to_selected_assistant(self):
        for actual, expected_error in (
            ("v1-receiver", None),
            ("build", "native_agent_mismatch"),
            (None, "native_agent_metadata_invalid"),
            ("x" * 1000, "native_agent_metadata_invalid"),
        ):
            self.db.execute(
                "UPDATE message SET data=? WHERE id='a'",
                (
                    json.dumps(
                        dict(role="assistant", parentID="u", time={"completed": 1}, agent=actual)
                    ),
                ),
            )
            self.db.commit()
            if expected_error:
                with self.assertRaisesRegex(probe.ProbeError, expected_error):
                    probe._native_parts(
                        self.path, NONCE, time.monotonic() + 2, expected_agent="v1-receiver"
                    )
            else:
                self.assertEqual(
                    probe._native_parts(
                        self.path, NONCE, time.monotonic() + 2, expected_agent="v1-receiver"
                    ),
                    [probe.MARKER + "\n" + NONCE],
                )

    def test_observer_timing_uses_one_finishing_timestamp(self):
        identity = self.path.stat()
        for finish, deadline, within_three, within_original in (
            (4.0, 10.0, False, True),
            (2.5, 2.0, False, False),
            (2.9, 10.0, True, True),
        ):
            with (
                self.subTest(finish=finish, deadline=deadline),
                patch.object(probe.time, "monotonic", side_effect=[0.0, finish]) as clock,
                patch.object(
                    probe, "_digest", return_value=probe.hashlib.sha256(NONCE.encode()).hexdigest()
                ),
                patch.object(
                    probe, "_owned_db", return_value=(self.path, (identity.st_dev, identity.st_ino))
                ),
                patch.object(probe, "_native_parts", return_value=["ACK\n" + NONCE]),
            ):
                result = probe.observe(
                    Path(self.temp.name),
                    Path(self.temp.name),
                    "receiver",
                    None,
                    NONCE,
                    "ACK",
                    deadline,
                    expected="ACK",
                    expected_agent="v1-receiver",
                )
                self.assertIs(result["within_three_seconds"], within_three)
                self.assertIs(result["within_original_deadline"], within_original)
                self.assertEqual(clock.call_count, 2)

    def test_native_profile_gate_requires_all_four_exact_bindings(self):
        good = {
            "native_text_diagnostic": {"status": "observed", "native_agent_matches": True},
            "receiver_native_text_diagnostic": {"status": "observed", "native_agent_matches": True},
        }
        for diagnostic in good.values():
            diagnostic.update(
                active_receipt_bound=True,
                exact_owned_root=True,
                within_three_seconds=True,
                within_original_deadline=True,
            )
        self.assertTrue(probe.native_profiles_exact([good, good]))
        for phases in (
            [],
            [good],
            [good, {}],
            [
                good,
                {
                    **good,
                    "receiver_native_text_diagnostic": {
                        "status": "observed",
                        "native_agent_matches": 1,
                    },
                },
            ],
        ):
            self.assertFalse(probe.native_profiles_exact(phases))

    def test_native_profile_gate_rejects_invalid_binding_and_timing(self):
        required = (
            "active_receipt_bound",
            "exact_owned_root",
            "within_three_seconds",
            "within_original_deadline",
        )
        good = dict(status="observed", native_agent_matches=True, **{key: True for key in required})
        for index in range(2):
            for role in ("native_text_diagnostic", "receiver_native_text_diagnostic"):
                for key in required:
                    for value in (False, None, 1, "missing"):
                        with self.subTest(index=index, role=role, key=key, value=value):
                            phases = [
                                {
                                    name: dict(good)
                                    for name in (
                                        "native_text_diagnostic",
                                        "receiver_native_text_diagnostic",
                                    )
                                }
                                for _ in range(2)
                            ]
                            if value == "missing":
                                del phases[index][role][key]
                            else:
                                phases[index][role][key] = value
                            self.assertFalse(probe.native_profiles_exact(phases))

    def test_native_agent_identity_uses_receipt_bearing_sibling_only(self):
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (
                json.dumps(
                    dict(role="assistant", parentID="u", time={"completed": 1}, agent="v1-receiver")
                ),
            ),
        )
        self.message("sibling", "assistant", parentID="u", time={"completed": 2}, agent="build")
        self.part("sibling", "sibpart", "not the receipt")
        self.db.commit()
        self.assertEqual(
            probe._native_parts(
                self.path, NONCE, time.monotonic() + 2, expected_agent="v1-receiver"
            ),
            [probe.MARKER + "\n" + NONCE],
        )
        self.db.execute(
            "UPDATE message SET data=? WHERE id='a'",
            (
                json.dumps(
                    dict(role="assistant", parentID="u", time={"completed": 1}, agent="build")
                ),
            ),
        )
        self.db.execute(
            "UPDATE message SET data=? WHERE id='sibling'",
            (
                json.dumps(
                    dict(role="assistant", parentID="u", time={"completed": 2}, agent="v1-receiver")
                ),
            ),
        )
        self.db.commit()
        with self.assertRaisesRegex(probe.ProbeError, "native_agent_mismatch"):
            probe._native_parts(
                self.path, NONCE, time.monotonic() + 2, expected_agent="v1-receiver"
            )


if __name__ == "__main__":
    unittest.main()
