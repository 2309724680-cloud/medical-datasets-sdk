# Medical Datasets SDK

Dependency-free Python SDK for authenticated dataset upload and download.

## Install

```bash
python -m pip install -U git+https://github.com/2309724680-cloud/medical-datasets-sdk.git
```

## Authentication

Create an SDK Key in the platform, then set it as an environment variable:

```bash
export MEDICAL_DATASETS_SDKEY="your-sdk-key"
```

Windows PowerShell:

```powershell
$env:MEDICAL_DATASETS_SDKEY="your-sdk-key"
```

## Download

```python
from medical_datasets_sdk import MedicalDatasetsClient

client = MedicalDatasetsClient("http://10.20.13.1:24174")
path = client.download_dataset("example-dataset", destination="./datasets")
print(path)
```

Interrupted downloads use a `.part` file and resume automatically when the server supports HTTP ranges.

## Upload

```python
from medical_datasets_sdk import MedicalDatasetsClient

client = MedicalDatasetsClient("http://10.20.13.1:24174")
result = client.upload_dataset(
    "./dataset-folder",
    name="Example Dataset",
    source_name="Research Team",
    summary="Example SDK upload",
    category="Other",
    progress=lambda path, done, total: print(path, done, total),
)
print(result["revision"]["id"])
```

The client supports directory traversal, chunked file upload, server-side analysis, revision commits, recursive download, and resumable file downloads.

## Key handling

- Never commit an SDK Key to source control.
- Prefer `MEDICAL_DATASETS_SDKEY` over passing a key in code.
- Revoke exposed or unused keys from the platform immediately.
- The preview environment is not a production credential boundary.
