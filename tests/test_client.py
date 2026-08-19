import os
import tempfile
import unittest
import urllib.error
import urllib.request
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

    def test_upload_infers_new_dataset_name_from_local_directory(self):
        client = MedicalDatasetsClient("http://platform.example", token="sdk-secret")
        client.create_dataset = MagicMock(return_value={"id": 9, "slug": "scan-batch"})
        client.import_directory = MagicMock(return_value={"dataset": {"slug": "scan-batch"}, "revision": {"id": "revision-3"}})

        result = client.upload_dataset(Path("C:/datasets/scan-batch"))

        self.assertEqual(result["revision"]["id"], "revision-3")
        self.assertEqual(client.create_dataset.call_args.args[0], "scan-batch")

    def test_multipart_upload_skips_completed_parts_and_completes(self):
        client = MedicalDatasetsClient("http://platform.example", token="sdk-secret")
        responses = [
            {"partSizeBytes": 4, "maxParts": 10000, "parts": [{"partNumber": 1, "sizeBytes": 4}]},
            {"url": "http://minio.example/part-2"},
            {"url": "http://minio.example/part-3"},
            {"status": "ready"},
        ]
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "large.bin")
            source.write_bytes(b"abcdefghijkl")
            with patch.object(client, "_json_request", side_effect=responses) as api, patch("urllib.request.urlopen", return_value=response) as urlopen:
                client._upload_multipart("upload-1", {"id": "file-1"}, source, "large.bin", 12, None)

        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(urlopen.call_args_list[0].args[0].full_url, "http://minio.example/part-2")
        self.assertIn("/complete", api.call_args_list[-1].args[1])

    def test_download_reissues_url_and_resumes_after_interruption(self):
        client = MedicalDatasetsClient("http://platform.example", token="sdk-secret")

        class InterruptedResponse:
            status = 200
            headers = {"Content-Length": "10"}
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size):
                if hasattr(self, "sent"):
                    raise urllib.error.URLError("connection reset")
                self.sent = True
                return b"hello"

        class ResumedResponse:
            status = 206
            headers = {"Content-Length": "5"}
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _size):
                if hasattr(self, "sent"): return b""
                self.sent = True
                return b"world"

        with tempfile.TemporaryDirectory() as directory, patch.object(client, "_fresh_download_request", side_effect=[urllib.request.Request("http://minio/first"), urllib.request.Request("http://minio/second")]), patch("urllib.request.urlopen", side_effect=[InterruptedResponse(), ResumedResponse()]):
            target = client.download_file("dataset", "large.bin", Path(directory, "large.bin"), expected_size=10)
            self.assertEqual(target.read_bytes(), b"helloworld")


if __name__ == "__main__":
    unittest.main()
