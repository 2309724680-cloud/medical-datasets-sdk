import os
import unittest
from unittest.mock import patch

from medical_datasets_sdk import MedicalDatasetsClient, MedicalDatasetsError


class ClientTests(unittest.TestCase):
    def test_reads_sdkey_from_environment(self):
        with patch.dict(os.environ, {"MEDICAL_DATASETS_SDKEY": "sdk-secret"}, clear=True):
            client = MedicalDatasetsClient("http://platform.example")

        self.assertEqual(client.token, "sdk-secret")

    def test_sdkey_environment_precedes_legacy_token(self):
        with patch.dict(
            os.environ,
            {
                "MEDICAL_DATASETS_SDKEY": "new-key",
                "MEDICAL_DATASETS_TOKEN": "legacy-key",
            },
            clear=True,
        ):
            client = MedicalDatasetsClient("http://platform.example")

        self.assertEqual(client.token, "new-key")

    def test_request_uses_bearer_authorization(self):
        client = MedicalDatasetsClient("http://platform.example/", token="sdk-secret")
        request = client._request("/api/datasets")

        self.assertEqual(request.full_url, "http://platform.example/api/datasets")
        self.assertEqual(request.get_header("Authorization"), "Bearer sdk-secret")

    def test_rejects_unsafe_server_path(self):
        with self.assertRaises(MedicalDatasetsError):
            MedicalDatasetsClient._local_relative_path("../outside")

    def test_sanitizes_local_dataset_folder(self):
        self.assertEqual(
            MedicalDatasetsClient._safe_folder_name('CT:Study/2026*?'),
            "CT_Study_2026__",
        )


if __name__ == "__main__":
    unittest.main()
