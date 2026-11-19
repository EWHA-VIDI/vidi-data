import hashlib
import hmac
import os
import unittest
from unittest.mock import patch

from src.collection.pseudonyms import Pseudonymizer


class PseudonymTests(unittest.TestCase):
    def setUp(self):
        self.key = b"test-only-key-not-for-collection!!"
        self.ids = Pseudonymizer(self.key)

    def test_same_account_links_across_shards_and_uploader(self):
        second_shard = Pseudonymizer(self.key)
        raw = "UCexample"
        expected = "ch_" + hmac.new(self.key, b"channel:UCexample", hashlib.sha256).hexdigest()
        self.assertEqual(self.ids.channel_id(raw), expected)
        self.assertEqual(second_shard.channel_id(raw), expected)
        self.assertEqual(self.ids.channel_id(raw), self.ids.channel_id(raw))

    def test_parent_and_comment_use_same_domain(self):
        self.assertEqual(self.ids.comment_id("parent"), self.ids.comment_id("parent"))
        self.assertNotEqual(self.ids.channel_id("parent")[3:], self.ids.comment_id("parent")[3:])
        self.assertIsNone(self.ids.channel_id(None))
        self.assertIsNone(self.ids.comment_id(None))
        self.assertNotEqual(self.ids.channel_id("UCexample"), self.ids.channel_id("UCExample"))

    def test_no_fallback_or_key_in_repr(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                Pseudonymizer.from_environment()
        with self.assertRaises(ValueError):
            Pseudonymizer(b"short")
        self.assertNotIn(self.key.decode(), repr(self.ids))

    def test_environment_key_and_version(self):
        with patch.dict(os.environ, {"DATA_PSEUDONYM_KEY": self.key.decode(), "DATA_PSEUDONYM_KEY_VERSION": "v2"}):
            value = Pseudonymizer.from_environment()
        self.assertEqual(value.key_version, "v2")
        self.assertEqual(value.channel_id("example"), self.ids.channel_id("example"))

    def test_rehash_and_invalid_raw_input_rejected(self):
        for value in ["", " raw", 42, self.ids.channel_id("x"), self.ids.comment_id("x")]:
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                self.ids.channel_id(value)
