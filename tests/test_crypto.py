from __future__ import annotations

from django.db import connection
from django.test import TestCase

from apps.servers.models import SSHKey


class EncryptedFieldTests(TestCase):
    def test_round_trip(self):
        key = SSHKey.objects.create(name="prod", private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
        key.refresh_from_db()
        self.assertIn("BEGIN OPENSSH", key.private_key)

    def test_stored_value_is_encrypted(self):
        SSHKey.objects.create(name="prod", private_key="super-secret")
        with connection.cursor() as cursor:
            cursor.execute("SELECT private_key FROM servers_sshkey")
            raw = cursor.fetchone()[0]
        self.assertTrue(raw.startswith("enc:v1:"))
        self.assertNotIn("super-secret", raw)

    def test_saving_twice_does_not_double_encrypt(self):
        key = SSHKey.objects.create(name="prod", private_key="secret")
        key.save()
        key.refresh_from_db()
        self.assertEqual(key.private_key, "secret")
        with connection.cursor() as cursor:
            cursor.execute("SELECT private_key FROM servers_sshkey")
            raw = cursor.fetchone()[0]
        self.assertEqual(raw.count("enc:v1:"), 1)
