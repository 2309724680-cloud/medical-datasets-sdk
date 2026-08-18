import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from medical_datasets_sdk.cli import main


class CliTests(unittest.TestCase):
    @patch("medical_datasets_sdk.cli.MedicalDatasetsClient")
    def test_auth_check(self, client_type):
        client_type.return_value.whoami.return_value = {"username": "sdk-user"}
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--base-url", "http://platform.example", "auth-check"])

        self.assertEqual(code, 0)
        self.assertIn("sdk-user", output.getvalue())

    @patch("medical_datasets_sdk.cli.MedicalDatasetsClient")
    def test_upload_command(self, client_type):
        client_type.return_value.upload_dataset.return_value = {
            "dataset": {"slug": "example"},
            "revision": {"id": "revision-1"},
        }
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["upload", "./dataset", "--name", "Example"])

        self.assertEqual(code, 0)
        self.assertIn("revision-1", output.getvalue())
        self.assertEqual(client_type.return_value.upload_dataset.call_args.kwargs["name"], "Example")

    @patch("medical_datasets_sdk.cli.MedicalDatasetsClient")
    def test_upload_command_infers_dataset_name(self, client_type):
        client_type.return_value.upload_dataset.return_value = {
            "dataset": {"slug": "dataset-folder"},
            "revision": {"id": "revision-2"},
        }
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["upload", "./dataset-folder"])

        self.assertEqual(code, 0)
        self.assertIn("revision-2", output.getvalue())
        self.assertEqual(client_type.return_value.upload_dataset.call_args.kwargs["name"], "dataset-folder")


if __name__ == "__main__":
    unittest.main()
