"""Small, dependency-free client for the Medical Datasets catalog API."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator


ProgressCallback = Callable[[str, int, int], None]


class MedicalDatasetsError(RuntimeError):
    """Raised when the catalog API rejects a request."""


class MedicalDatasetsClient:
    """Browse and download catalog datasets over HTTP.

    Args:
        base_url: Catalog address, for example ``http://catalog.internal:4173``.
        token: Optional SDK key. When omitted, ``MEDICAL_DATASETS_SDKEY`` is used,
            falling back to the legacy ``MEDICAL_DATASETS_TOKEN``.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.getenv("MEDICAL_DATASETS_SDKEY", os.getenv("MEDICAL_DATASETS_TOKEN", ""))
        self.timeout = timeout

    def get_dataset(self, slug: str) -> dict:
        return self._json(f"/api/datasets/{self._quote_slug(slug)}")

    def create_dataset(
        self,
        name: str,
        source: str,
        *,
        summary: str = "",
        description: str = "",
        category: str = "其他",
        modalities: list[str] | None = None,
        formats: list[str] | None = None,
        tags: list[str] | None = None,
        readme: str = "",
        defer_initial_revision: bool = False,
    ) -> dict:
        """Create a managed dataset record.

        Set ``defer_initial_revision`` when files will be uploaded immediately.
        The draft remains hidden until the first import is committed.
        """
        return self._json_request("POST", "/api/datasets", {
            "name": name,
            "source": source,
            "summary": summary,
            "description": description,
            "category": category,
            "modalities": modalities or [],
            "formats": formats or [],
            "tags": tags or [],
            "readme": readme,
            "recordType": "dataset",
            "deferInitialRevision": bool(defer_initial_revision),
        })

    def upload_dataset(
        self,
        source: str | os.PathLike[str],
        *,
        dataset_slug: str | None = None,
        name: str | None = None,
        source_name: str = "SDK 上传",
        summary: str = "",
        description: str = "",
        readme: str = "",
        category: str = "其他",
        modalities: list[str] | None = None,
        formats: list[str] | None = None,
        tags: list[str] | None = None,
        message: str = "SDK 上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        poll_interval: float = 0.8,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload, organize, and publish in one call.

        Pass ``dataset_slug`` to append to an existing dataset. If omitted, pass
        ``name`` to create and publish a new dataset. ``source`` may be one file,
        an archive, or a directory. The returned dictionary contains the committed
        revision and dataset metadata.
        """
        if dataset_slug and name:
            raise ValueError("dataset_slug 和 name 只能填写一个")
        is_new_dataset = not dataset_slug
        if is_new_dataset and not str(name or "").strip():
            raise ValueError("新建数据集必须填写 name；追加已有数据集请填写 dataset_slug")

        slug = dataset_slug
        if is_new_dataset:
            dataset = self.create_dataset(
                str(name).strip(),
                source_name,
                summary=summary,
                description=description,
                category=category,
                modalities=modalities,
                formats=formats,
                tags=tags,
                readme=readme,
                defer_initial_revision=True,
            )
            slug = dataset["slug"]

        try:
            return self.import_directory(
                str(slug),
                source,
                message=message,
                progress=progress,
                chunk_size=chunk_size,
                keep_archives=keep_archives,
                confirm=True,
                new_dataset=is_new_dataset,
                poll_interval=poll_interval,
                exclude=exclude,
            )
        except Exception:
            if is_new_dataset:
                try:
                    self._json_request("DELETE", f"/api/datasets/{dataset['id']}")
                except MedicalDatasetsError:
                    pass
            raise

    def upload_directory(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传目录",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
    ) -> dict:
        """Backward-compatible alias for :meth:`import_directory`."""
        return self.import_directory(
            slug,
            source,
            message=message,
            progress=progress,
            chunk_size=chunk_size,
            keep_archives=keep_archives,
            confirm=True,
        )

    def analyze_upload(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        poll_interval: float = 0.8,
        new_dataset: bool = False,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload a file or directory and wait for an organization plan.

        ZIP, RAR, TAR, TAR.GZ and TGZ inputs are extracted in an isolated server
        workspace. Nothing is committed until :meth:`commit_import` is called.
        """
        dataset = self.get_dataset(slug)
        if new_dataset and (dataset.get("migrationStatus") != "draft" or dataset.get("contentRevision")):
            raise MedicalDatasetsError("new_dataset 只能用于尚未发布的草稿数据集")
        upload_id: str | None = None
        try:
            source_path = Path(source).expanduser().resolve()
            if not source_path.exists() or source_path.is_symlink():
                raise MedicalDatasetsError(f"上传来源不存在或不可用: {source_path}")
            if source_path.is_dir():
                excluded = {str(item).strip() for item in exclude if str(item).strip()}
                files = sorted(
                    path for path in source_path.rglob("*")
                    if path.is_file()
                    and not path.is_symlink()
                    and not any(part in excluded for part in path.relative_to(source_path).parts)
                )
                upload_entries = [(path, path.relative_to(source_path).as_posix()) for path in files]
            elif source_path.is_file():
                upload_entries = [(source_path, source_path.name)]
            else:
                raise MedicalDatasetsError(f"上传来源不是普通文件或目录: {source_path}")
            if not upload_entries:
                raise MedicalDatasetsError("上传目录中没有普通文件")
            session = self._json_request(
                "POST", f"/api/datasets/{dataset['id']}/uploads", {"message": message}
            )
            upload_id = session["id"]
            server_limit = int(session.get("limits", {}).get("maxChunkBytes") or chunk_size)
            actual_chunk_size = max(1024 * 1024, min(int(chunk_size), server_limit))
            for local_path, relative_path in upload_entries:
                size_bytes = local_path.stat().st_size
                remote = self._json_request(
                    "POST",
                    f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files",
                    {"path": relative_path, "sizeBytes": size_bytes},
                )
                uploaded = int(remote.get("uploadedBytes", 0))
                with local_path.open("rb") as handle:
                    handle.seek(uploaded)
                    while uploaded < size_bytes:
                        chunk = handle.read(min(actual_chunk_size, size_bytes - uploaded))
                        if not chunk:
                            raise MedicalDatasetsError(f"读取上传文件失败: {relative_path}")
                        result = self._json_request(
                            "PATCH",
                            f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/files/{urllib.parse.quote(remote['id'], safe='')}",
                            data=chunk,
                            headers={
                                "Content-Type": "application/octet-stream",
                                "Upload-Offset": str(uploaded),
                            },
                        )
                        uploaded = int(result["uploadedBytes"])
                        if progress:
                            progress(relative_path, uploaded, size_bytes)
            job = self._json_request(
                "POST",
                f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}/analyze",
                {"keepArchives": bool(keep_archives), "newDataset": bool(new_dataset)},
            )
            while job.get("status") in {"analyzing", "extracting"}:
                time.sleep(max(0.2, float(poll_interval)))
                job = self.get_import(upload_id)
            if job.get("status") != "ready":
                raise MedicalDatasetsError(job.get("error") or "平台未能生成整理方案")
            return job
        except Exception:
            try:
                if upload_id:
                    self._json_request(
                        "DELETE",
                        f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}"
                        f"{'?removeDataset=1' if new_dataset else ''}",
                    )
                elif new_dataset:
                    self._json_request("DELETE", f"/api/datasets/{dataset['id']}")
            except MedicalDatasetsError:
                pass
            raise

    def get_import(self, upload_id: str) -> dict:
        """Return the current analysis or commit state for an upload."""
        return self._json(f"/api/imports/{urllib.parse.quote(upload_id, safe='')}")

    def commit_import(self, upload_id: str) -> dict:
        """Atomically commit a prepared organization plan as a new revision."""
        return self._json_request(
            "POST",
            f"/api/imports/{urllib.parse.quote(upload_id, safe='')}/commit",
            {},
        )

    def cancel_import(self, upload_id: str, *, remove_dataset: bool = False) -> None:
        """Cancel analysis and remove its isolated upload workspace."""
        suffix = "?removeDataset=1" if remove_dataset else ""
        self._json_request("DELETE", f"/api/uploads/{urllib.parse.quote(upload_id, safe='')}{suffix}")

    def import_directory(
        self,
        slug: str,
        source: str | os.PathLike[str],
        *,
        message: str = "上传并整理文件",
        progress: ProgressCallback | None = None,
        chunk_size: int = 16 * 1024 * 1024,
        keep_archives: bool = False,
        confirm: bool = True,
        new_dataset: bool = False,
        poll_interval: float = 0.8,
        exclude: tuple[str, ...] = (".git", ".DS_Store", "Thumbs.db", "node_modules"),
    ) -> dict:
        """Upload, analyze and optionally commit a file or directory."""
        job = self.analyze_upload(
            slug,
            source,
            message=message,
            progress=progress,
            chunk_size=chunk_size,
            keep_archives=keep_archives,
            new_dataset=new_dataset,
            poll_interval=poll_interval,
            exclude=exclude,
        )
        return self.commit_import(job["id"]) if confirm else job

    def iter_directory(self, slug: str, path: str = "") -> Iterator[dict]:
        """Yield every entry in a directory, transparently following API pages."""
        offset = 0
        while True:
            query = urllib.parse.urlencode({"path": path, "offset": offset, "limit": 500})
            page = self._json(f"/api/datasets/{self._quote_slug(slug)}/files?{query}")
            yield from page["entries"]
            if not page.get("hasMore"):
                return
            offset += len(page["entries"])

    def download_dataset(
        self,
        slug: str,
        destination: str | os.PathLike[str] = ".",
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Recursively download a dataset and return its local directory.

        Existing complete files are skipped. Interrupted files are stored with a
        ``.part`` suffix and resumed on the next call when the server supports it.
        """
        dataset = self.get_dataset(slug)
        dataset_dir = Path(destination).expanduser() / self._safe_folder_name(dataset["name"])
        dataset_dir.mkdir(parents=True, exist_ok=True)

        pending = [""]
        while pending:
            directory = pending.pop()
            for entry in self.iter_directory(slug, directory):
                relative_path = str(PurePosixPath(directory, entry["name"]))
                if entry["type"] == "directory":
                    (dataset_dir / self._local_relative_path(relative_path)).mkdir(parents=True, exist_ok=True)
                    pending.append(relative_path)
                elif entry["type"] == "file":
                    self.download_file(
                        slug,
                        relative_path,
                        dataset_dir / self._local_relative_path(relative_path),
                        expected_size=entry.get("sizeBytes"),
                        progress=progress,
                    )
        return dataset_dir

    def download_file(
        self,
        slug: str,
        path: str,
        destination: str | os.PathLike[str],
        expected_size: int | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Download one file atomically, resuming an existing partial download."""
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        if expected_size is not None and target.is_file() and target.stat().st_size == expected_size:
            if progress:
                progress(path, expected_size, expected_size)
            return target

        partial = target.with_name(f"{target.name}.part")
        downloaded = partial.stat().st_size if partial.is_file() else 0
        if expected_size is not None and downloaded > expected_size:
            partial.unlink()
            downloaded = 0
        if expected_size is not None and downloaded == expected_size:
            partial.replace(target)
            return target

        query = urllib.parse.urlencode({"path": path})
        request = self._request(f"/api/datasets/{self._quote_slug(slug)}/download?{query}")
        if downloaded:
            request.add_header("Range", f"bytes={downloaded}-")

        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            raise self._api_error(error) from error
        except urllib.error.URLError as error:
            raise MedicalDatasetsError(f"无法连接数据集平台: {error.reason}") from error

        with response:
            append = downloaded > 0 and response.status == 206
            if not append:
                downloaded = 0
            content_length = int(response.headers.get("Content-Length", "0"))
            total = expected_size or (downloaded + content_length)
            with partial.open("ab" if append else "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(path, downloaded, total)

        if expected_size is not None and partial.stat().st_size != expected_size:
            raise MedicalDatasetsError(
                f"下载大小不一致: {path}（预期 {expected_size}，实际 {partial.stat().st_size}）"
            )
        partial.replace(target)
        return target

    def _json(self, path: str) -> dict:
        return self._json_request("GET", path)

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        if payload is not None and data is not None:
            raise ValueError("payload and data cannot both be provided")
        body = data
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = self._request(path, method=method, data=body, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return {}
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise self._api_error(error) from error
        except urllib.error.URLError as error:
            raise MedicalDatasetsError(f"无法连接数据集平台: {error.reason}") from error

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        request.add_header("Accept", "application/json, application/octet-stream")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        return request

    @staticmethod
    def _api_error(error: urllib.error.HTTPError) -> MedicalDatasetsError:
        try:
            message = json.loads(error.read().decode("utf-8")).get("error")
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = None
        return MedicalDatasetsError(message or f"数据集平台请求失败 ({error.code})")

    @staticmethod
    def _quote_slug(slug: str) -> str:
        return urllib.parse.quote(str(slug), safe="")

    @staticmethod
    def _safe_folder_name(name: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", str(name)).strip(". ")
        return cleaned or "dataset"

    @staticmethod
    def _local_relative_path(value: str) -> Path:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise MedicalDatasetsError(f"服务端返回了不安全的文件路径: {value}")
        return Path(*path.parts)
