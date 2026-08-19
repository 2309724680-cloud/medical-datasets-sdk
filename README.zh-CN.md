[![English](https://img.shields.io/badge/Language-English-0969da?style=for-the-badge)](./README.md)

# 医疗数据集 SDK

这是一个可通过 `pip` 安装的医疗数据集 SDK 命令行工具。SDK 使用独立、可限定权限的 SDK Key，并通过预签名 URL 与 S3/MinIO 对象存储直接传输文件。

安装完成后直接使用 `medical-datasets` 命令，不需要下载、编写或维护单独的 Python 上传下载脚本。

## 安装

直接从公开的 GitHub 仓库安装：

```bash
python -m pip install -U git+https://github.com/2309724680-cloud/medical-datasets-sdk.git
```

检查安装版本：

```bash
python -m medical_datasets_sdk.cli --version
```

### Windows 命令路径

Windows 的用户级 Python 安装可能不会自动把命令目录加入 `PATH`。在当前 PowerShell 中执行：

```powershell
$sdkScripts = python -c "import sysconfig; print(sysconfig.get_path('scripts', scheme='nt_user'))"
$env:Path = "$sdkScripts;$env:Path"
medical-datasets --version
```

永久加入当前用户的 `PATH`：

```powershell
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$sdkScripts*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$sdkScripts", "User")
}
```

## 配置 SDK Key

先在医疗数据集平台创建 SDK Key，并按实际用途选择 `read`、`download`、`upload` 权限。

Linux 或 macOS：

```bash
export MEDICAL_DATASETS_SDKEY="your-sdk-key"
```

Windows PowerShell 推荐使用安全输入，避免 Key 出现在命令历史中：

```powershell
$secureKey = Read-Host "输入 SDK Key" -AsSecureString
$key = [Net.NetworkCredential]::new("", $secureKey).Password.Trim()
$env:MEDICAL_DATASETS_SDKEY = $key
```

验证身份：

```bash
medical-datasets auth-check
```

环境变量默认只在当前终端中有效。不要把 SDK Key 写进代码、聊天、截图、Git 仓库或普通脚本文件。Key 一旦泄露，应立即在平台撤销并重新创建。

## 配置平台地址

SDK 默认连接预览平台：

```text
http://10.20.13.1:24174
```

需要连接其他环境时设置：

```powershell
$env:MEDICAL_DATASETS_URL = "http://10.20.13.1:24174"
```

也可以在命令中指定：

```bash
medical-datasets --base-url http://10.20.13.1:24174 auth-check
```

## 命令行上传

创建新数据集：

```powershell
py -3 -m medical_datasets_sdk.cli upload "C:\Users\86131\Desktop\测试2" --name "测试2" --source-name "影像科项目"
```

新建数据集时需要填写数据集名称和数据来源，其他网页元数据无需填写。

大文件会自动使用 S3 Multipart 分片上传。网络中断或终端关闭后，重新执行同一条命令即可跳过已完成分片继续上传。平台支持最大 5 TiB 的单个对象；数据集超过 10 GiB 时请优先使用 SDK。

Linux 或 macOS：

```bash
medical-datasets upload ./dataset-folder \
  --name "胸部 CT 训练集" \
  --source-name "项目组"
```

上传文件到已有数据集：

```bash
medical-datasets upload ./new-files --dataset-slug example-dataset
```

SDK 会执行以下流程：

1. 使用 SDK Key 向平台申请短期预签名 URL。
2. 将文件直接上传到 MinIO，不经过浏览器上传页面。
3. 由平台校验、分析并整理文件。
4. 提交不可变的数据集 revision。

## 命令行下载

```powershell
medical-datasets download example-dataset --destination "C:\Users\86131\Desktop\下载目录"
```

下载会优先使用 MinIO 预签名 URL。中断的文件使用 `.part` 临时文件，并在服务端支持 HTTP Range 时自动续传。
对象存储 URL 过期后 SDK 会自动重新签名，并保留 `.part` 文件继续下载。

### 超大数据集容量规划

- 单文件支持上限为 5 TiB；1 TiB 文件默认拆分为 8,192 个 128 MiB 分片。
- 上传会话默认保留 30 天，重新执行相同命令会复用未完成会话。
- 当前平台会在 MinIO 主存储之外保留一份本地可浏览仓库，容量规划按数据集净容量的至少 2 倍计算，并额外预留解压空间。
- 超大压缩包解压可能临时需要接近“压缩包 + 解压内容 + 主存储对象”的容量，生产环境建议上传已展开目录。

## SDK Key 安全规则

- 不要把 SDK Key 提交到源代码仓库。
- 优先通过 `MEDICAL_DATASETS_SDKEY` 环境变量配置 Key。
- 自动化任务只分配实际需要的 `read`、`download`、`upload` 权限。
- 不同设备和任务使用不同 Key，便于单独撤销和审计。
- Key 被撤销后会立即失效，并从用户的有效 Key 列表中消失。
- MinIO Bucket 保持私有，用户必须先通过平台鉴权才能获得短期访问 URL。
