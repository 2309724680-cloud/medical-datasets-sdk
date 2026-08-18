[![中文](https://img.shields.io/badge/语言-中文-d73a49?style=for-the-badge)](./README.zh-CN.md)

# Medical Datasets SDK

Pip-installable SDK command line tool for authenticated dataset upload and download. It uses independent scoped SDK Keys and presigned S3/MinIO transfers. Users do not need to download or maintain a standalone Python script.

## Install

```bash
python -m pip install -U git+https://github.com/2309724680-cloud/medical-datasets-sdk.git
```

On Windows, user-level Python installations may place the command outside the current `PATH`. Add it for the current PowerShell session:

```powershell
$sdkScripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
$env:Path = "$sdkScripts;$env:Path"
medical-datasets --version
```

## Authentication

Create an SDK Key in the platform, then set it as an environment variable:

```bash
export MEDICAL_DATASETS_SDKEY="your-sdk-key"
```

Windows PowerShell:

```powershell
$secureKey = Read-Host "Input SDK Key" -AsSecureString
$key = [Net.NetworkCredential]::new("", $secureKey).Password.Trim()
$env:MEDICAL_DATASETS_SDKEY = $key
```

The environment variable is available only in the current terminal. Do not paste SDK Keys into chat, source code, screenshots, or shell scripts.

Validate the key:

```bash
medical-datasets auth-check
```

Use another platform URL when needed:

```powershell
$env:MEDICAL_DATASETS_URL = "http://10.20.13.1:24174"
```

## Command line upload

No Python script is required after installation:

```bash
medical-datasets upload ./dataset-folder \
  --name "Example Dataset" \
  --source-name "Research Team" \
  --category "Other"
```

Windows PowerShell example:

```powershell
medical-datasets upload "C:\Users\86131\Desktop\测试2" --name "测试2" --source-name "SDK上传" --category "Other"
```

Upload to an existing dataset:

```bash
medical-datasets upload ./new-files --dataset-slug example-dataset
```

## Command line download

```bash
medical-datasets download example-dataset --destination ./datasets
```

Interrupted downloads use a `.part` file and resume automatically when the server supports HTTP ranges.

## Key handling

- Never commit an SDK Key to source control.
- Prefer `MEDICAL_DATASETS_SDKEY` over passing a key in code.
- Revoke exposed or unused keys from the platform immediately.
- Give automation only the scopes it needs: `read`, `download`, and/or `upload`.
- The preview environment is not a production credential boundary.
