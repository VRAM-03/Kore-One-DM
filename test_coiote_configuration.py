import unittest
from hashlib import pbkdf2_hmac

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from coiote_configuration import CoioteCredentialHashes
from main import app, get_db


class CoioteConfigurationTests(unittest.TestCase):
    path = "/connectors/coiote/configuration"
    origin = "http://localhost:4200"

    def setUp(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(engine.dispose)
        CoioteCredentialHashes.__table__.create(engine)
        self.engine = engine

        def db_override():
            with Session(engine) as db:
                yield db

        app.dependency_overrides[get_db] = db_override
        self.addCleanup(app.dependency_overrides.pop, get_db)
        self.client = TestClient(app, base_url=self.origin, client=("127.0.0.1", 50000))
        self.addCleanup(self.client.close)

    def test_saves_and_replaces_only_salted_hashes_without_returning_values(self):
        sample = "test-value-only"
        previous = None
        for _ in range(2):
            response = self.client.put(
                self.path, headers={"Origin": self.origin},
                json={"username": sample, "password": sample},
            )
            self.assertEqual(response.status_code, 204)
            self.assertFalse(response.content)
            with Session(self.engine) as db:
                row = db.query(CoioteCredentialHashes).one()
                hashes = (row.username_hash, row.password_hash)
            self.assertNotEqual(*hashes)
            self.assertNotEqual(hashes, previous)
            for encoded in hashes:
                self.assertNotIn(sample, encoded)
                algorithm, iterations, salt, digest = encoded.split("$")
                self.assertEqual(algorithm, "pbkdf2-sha256")
                self.assertGreaterEqual(int(iterations), 600_000)
                self.assertEqual(
                    pbkdf2_hmac("sha256", sample.encode(), bytes.fromhex(salt), int(iterations)).hex(),
                    digest,
                )
            previous = hashes

    def test_rejects_cross_origin_and_invalid_requests_without_storing_values(self):
        response = self.client.put(
            self.path, headers={"Origin": "http://localhost:9999"},
            json={"username": "test-account", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.put(
            self.path, headers={"Origin": self.origin},
            json={"username": " " * 255, "password": "test-password"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("test-password", response.text)
        with Session(self.engine) as db:
            self.assertEqual(db.query(CoioteCredentialHashes).count(), 0)
