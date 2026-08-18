# Medical Datasets SDK

Dependency-free Python SDK and CLI for authenticated dataset upload and download. It uses independent scoped SDK Keys and presigned S3/MinIO transfers.

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

Validate the key:

```bash
medical-datasets auth-check
```

## Command line upload

No Python script is required after installation:

```bash
medical-datasets upload ./dataset-folder \
  --name "Example Dataset" \
  --source-name "Research Team" \
  --category "Other"
```

Upload to an existing dataset:

```bash
medical-datasets upload ./new-files --dataset-slug example-dataset
```

## Command line download

```bash
medical-datasets download example-dataset --destination ./datasets
```

## Python API download

```python
from medical_datasets_sdk import MedicalDatasetsClient

client = MedicalDatasetsClient("http://10.20.13.1:24174")
path = client.download_dataset("example-dataset", destination="./datasets")
print(path)
```

Interrupted downloads use a `.part` file and resume automatically when the server supports HTTP ranges.

## Python API upload

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
- Give automation only the scopes it needs: `read`, `download`, and/or `upload`.
- The preview environment is not a production credential boundary.
