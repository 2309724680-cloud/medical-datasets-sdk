import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_upload_uses_presigned_object_url_when_available(self):
        client = MedicalDatasetsClient("http://platform.example", token="sdk-secret")
        direct_response = MagicMock()
        direct_response.__enter__.return_value = direct_response
        direct_response.__exit__.return_value = False
        direct_response.read.return_value = b""

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "sample.bin")
            source.write_bytes(b"medical-data")
            with patch.object(
                client,
                "_json_request",
                side_effect=[
                    {"id": 7, "slug": "sample", "migrationStatus": "ready", "contentRevision": "old"},
                    {"id": "upload-1", "limits": {"maxChunkBytes": 1024 * 1024}},
                    {"id": "file-1", "uploadUrl": "http://minio.example/object"},
                    {"id": "file-1", "status": "ready"},
                    {"id": "upload-1", "status": "ready"},
                ],
            ) as api, patch("urllib.request.urlopen", return_value=direct_response) as urlopen:
                result = client.analyze_upload("sample", source)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(urlopen.call_args.args[0].method, "PUT")
        self.assertTrue(api.call_args_list[2].args[2]["direct"])
        self.assertIn("/complete", api.call_args_list[3].args[1])


if __name__ == "__main__":
    unittest.main()
