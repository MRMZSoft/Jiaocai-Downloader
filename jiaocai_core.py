from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DATA_ROOT = "https://s-file-1.ykt.cbern.com.cn/zxx"
REQUEST_TIMEOUT = (10, 30)


class ResourceError(Exception):
    """可以直接显示给用户的解析或下载错误。"""


class DownloadCancelled(ResourceError):
    pass


def platform_host(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in ("ykt.cbern.com.cn", "smartedu.cn"))


def request_headers(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    headers = {"User-Agent": "Mozilla/5.0 Jiaocai-Downloader/2.0", "Accept-Encoding": "identity"}
    if not platform_host(parts.hostname or ""):
        return headers
    headers.update({
        "Authorization": "Bearer 0",
        "X-ND-AUTH": 'MAC id="0",nonce="0",mac="0"',
        "Origin": "https://basic.smartedu.cn",
        "Referer": "https://basic.smartedu.cn/",
    })
    return headers


@dataclass(frozen=True)
class Resource:
    title: str
    urls: tuple[str, ...]
    file_format: str
    resource_id: str = ""
    size: int = 0
    md5: str = ""


@dataclass
class Resolution:
    resources: list[Resource] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def page_details_url(page_url: str) -> tuple[str, str, str]:
    parts = urlsplit(page_url.strip())
    if parts.scheme not in ("http", "https") or not platform_host(parts.hostname or ""):
        raise ResourceError("请输入国家中小学智慧教育平台的完整资源页面网址。")
    params = parse_qs(parts.query)
    get = lambda key, default="": params.get(key, [default])[0]
    kind = get("contentType")
    resource_id = get("contentId")
    path = parts.path
    if not resource_id:
        if "/syncClassroom/classActivity" in path:
            resource_id, kind = get("activityId"), "national_lesson"
        elif "/syncClassroom/prepare/detail" in path:
            resource_id, kind = get("resourceId"), "prepare_sub_type"
        elif "/syncClassroom/detail" in path:
            resource_id, kind = get("resourceId"), get("resourceType")
        elif "/qualityCourse" in path:
            resource_id, kind = get("courseId"), "quality_course"
    kind = kind or "assets_document"
    if not re.fullmatch(r"[\w-]+", resource_id) or not re.fullmatch(r"[\w-]+", kind):
        raise ResourceError("网址缺少有效的资源 ID 或资源类型，请复制资源详情页的网址。")
    if "/tchMaterial/detail" in path and kind == "assets_document":
        route = f"ndrv2/resources/tch_material/details/{resource_id}.json"
    elif kind == "quality_course":
        route = f"ndrv2/resources/{resource_id}.json"
    elif kind in ("national_lesson", "prepare_sub_type") or "/syncClassroom/detail" in path:
        route = f"ndrv2/{kind}/resources/details/{resource_id}.json"
    else:
        route = f"ndrs/special_edu/resources/details/{resource_id}.json"
    return f"{DATA_ROOT}/{route}", resource_id, kind


def storage_urls(item: dict) -> tuple[str, ...]:
    values = [item.get("ti_storage")]
    storages = item.get("ti_storages") or []
    values.extend([storages] if isinstance(storages, str) else storages)
    urls = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        url = value.replace("cs_path:${ref-path}", "https://r1-ndr-private.ykt.cbern.com.cn")
        if urlsplit(url).scheme in ("http", "https") and url not in urls:
            urls.append(url)
    for url in tuple(urls):
        parts = urlsplit(url)
        if re.fullmatch(r"r[123]-ndr(?:-private)?\.ykt\.cbern\.com\.cn", parts.hostname or ""):
            for node in ("r1", "r2", "r3"):
                host = re.sub(r"^r[123]", node, parts.netloc)
                alternate = urlunsplit(parts._replace(netloc=host))
                if alternate not in urls:
                    urls.append(alternate)
    return tuple(urls)


def item_format(item: dict) -> str:
    value = str(item.get("ti_format") or item.get("lc_ti_format") or "").lower()
    if value == "application/pdf":
        return "pdf"
    if not re.fullmatch(r"[a-z0-9]{1,10}", value):
        value = Path(unquote(urlsplit(next(iter(storage_urls(item)), "")).path)).suffix[1:].lower()
    return value


def select_item(items: list[dict]) -> dict | None:
    usable = [item for item in items if isinstance(item, dict)
              and item_format(item) == "pdf" and storage_urls(item)]
    for item in usable:
        if item.get("ti_is_source_file") or item.get("ti_file_flag") == "source":
            return item
    return next(iter(usable), None)


def resource_title(data: dict) -> str:
    title = data.get("global_title")
    if isinstance(title, dict):
        title = title.get("zh-CN") or title.get("en") or next(iter(title.values()), "")
    return str(title or data.get("title") or data.get("id") or "未命名资源")


def safe_filename(title: str, extension: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "未命名资源"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.I):
        name = "_" + name
    extension = extension if re.fullmatch(r"[a-z0-9]{1,10}", extension) else "bin"
    return f"{name[:110].rstrip(' .')}.{extension}"


class SmartEduClient:
    def __init__(self, cancel: threading.Event | None = None, session: requests.Session | None = None):
        self.cancel = cancel if cancel is not None else threading.Event()
        self.session = session or requests.Session()
        if session is None:
            retry = Retry(total=2, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset({"GET"}))
            self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def __enter__(self) -> SmartEduClient:
        return self

    def __exit__(self, *args) -> None:
        self.session.close()

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise DownloadCancelled("下载已取消。")

    def get(self, url: str, **kwargs) -> requests.Response:
        self.check_cancelled()
        for _ in range(6):
            self.check_cancelled()
            response = self.session.get(url, headers=request_headers(url),
                                        timeout=REQUEST_TIMEOUT, allow_redirects=False, **kwargs)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            target = urljoin(url, response.headers.get("Location", ""))
            response.close()
            if urlsplit(target).scheme not in ("http", "https") or not response.headers.get("Location"):
                raise ResourceError("服务器返回了无效的跳转地址。")
            if urlsplit(url).scheme == "https" and urlsplit(target).scheme != "https":
                raise ResourceError("服务器返回了不安全的下载跳转。")
            url = target
        raise ResourceError("下载地址跳转次数过多。")

    def json_data(self, url: str):
        try:
            with self.get(url) as response:
                self.check_cancelled()
                response.raise_for_status()
                return response.json()
        except DownloadCancelled:
            raise
        except requests.exceptions.JSONDecodeError:
            raise ResourceError("平台返回的资源信息不是有效 JSON。") from None
        except requests.RequestException as error:
            code = getattr(error.response, "status_code", None)
            raise ResourceError(f"读取资源信息失败（HTTP {code}）。" if code else "读取资源信息失败，请检查网络后重试。") from None
        except ValueError:
            raise ResourceError("平台返回的资源信息不是有效 JSON。") from None

    def resolve(self, page_url: str) -> Resolution:
        endpoint, content_id, kind = page_details_url(page_url)
        data = self.json_data(endpoint)
        if not isinstance(data, dict):
            raise ResourceError("平台返回的资源详情格式不正确。")
        if str(data.get("status", "")).upper() in ("OFFLINE", "DELETED", "REMOVED"):
            raise ResourceError(f"《{resource_title(data)}》已下线或被移除，请在平台查找新版教材。")
        result = Resolution()

        def add(record: dict, parent: str = "") -> None:
            self.check_cancelled()
            if not isinstance(record, dict):
                return
            title = resource_title(record)
            if parent and " ".join(parent.split()).casefold() != " ".join(title.split()).casefold():
                title = f"{parent} - {title}"
            if str(record.get("status", "")).upper() in ("OFFLINE", "DELETED", "REMOVED"):
                result.warnings.append(f"《{title}》已下线，未提供可下载的 PDF 文件。")
                return
            item = select_item(record.get("ti_items") or [])
            if item:
                try:
                    size = max(0, int(item.get("ti_size") or 0))
                except (ValueError, TypeError):
                    size = 0
                md5 = str(item.get("ti_md5") or "")
                result.resources.append(Resource(title, storage_urls(item), item_format(item),
                                                 str(record.get("id") or content_id), size,
                                                 md5.lower() if re.fullmatch(r"[a-fA-F0-9]{32}", md5) else ""))
            elif not record.get("relations"):
                result.warnings.append(f"《{title}》未提供可下载的 PDF 文件。")
            relations = record.get("relations") or {}
            if isinstance(relations, dict):
                for group in relations.values():
                    if isinstance(group, list):
                        for child in group:
                            add(child, title)

        if kind == "thematic_course":
            children = self.json_data(f"{DATA_ROOT}/ndrs/special_edu/thematic_course/{content_id}/resources/list.json")
            if not isinstance(children, list):
                raise ResourceError("专题资源列表格式不正确。")
            for child in children:
                add(child, resource_title(data))
        else:
            add(data)
        unique = {}
        for resource in result.resources:
            unique.setdefault(resource.urls[0], resource)
        result.resources = list(unique.values())
        if not result.resources:
            raise ResourceError("\n".join(result.warnings) or "当前资源未提供可下载的文件。")
        return result

    def download(self, resource: Resource, destination: str | Path,
                 progress: Callable[[int, int], None] | None = None) -> Path:
        destination = Path(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ResourceError("无法创建保存目录，请检查路径和写入权限。") from None
        failures = []
        for url in resource.urls:
            self.check_cancelled()
            temporary = None
            try:
                with self.get(url, stream=True) as response:
                    self.check_cancelled()
                    if response.status_code in (401, 403):
                        raise ResourceError(f"平台拒绝访问该资源（HTTP {response.status_code}），资源可能已下线或不允许匿名下载。")
                    if response.status_code >= 400:
                        raise ResourceError(f"服务器返回 HTTP {response.status_code}。")
                    if response.status_code != 200:
                        raise ResourceError(f"服务器返回了非完整文件（HTTP {response.status_code}）。")
                    content_type = response.headers.get("Content-Type", "").lower()
                    if any(kind in content_type for kind in ("text/html", "application/xml", "text/xml", "application/json")):
                        raise ResourceError("服务器返回了错误页面，未保存为教材。")
                    try:
                        length = max(0, int(response.headers.get("Content-Length", 0)))
                    except (ValueError, TypeError):
                        length = 0
                    if response.headers.get("Content-Encoding", "identity").lower() not in ("identity", ""):
                        length = 0
                    total = length or resource.size
                    received = 0
                    digest = hashlib.md5()
                    prefix = bytearray()
                    last_update = 0.0
                    with tempfile.NamedTemporaryFile(mode="wb", prefix=".jiaocai-", suffix=".part",
                                                     dir=destination.parent, delete=False) as output:
                        temporary = Path(output.name)
                        for chunk in response.iter_content(chunk_size=131072):
                            self.check_cancelled()
                            if not chunk:
                                continue
                            if len(prefix) < 1024:
                                prefix.extend(chunk[:1024 - len(prefix)])
                            output.write(chunk)
                            digest.update(chunk)
                            received += len(chunk)
                            now = time.monotonic()
                            if progress and now - last_update >= 0.15:
                                progress(received, total)
                                last_update = now
                    self.check_cancelled()
                    if not received:
                        raise ResourceError("服务器返回了空文件。")
                    beginning = bytes(prefix).lstrip().lower()
                    if beginning.startswith((b"<html", b"<!doctype html", b"<?xml", b"<error", b'{"error')):
                        raise ResourceError("文件内容是错误页面，未保存为教材。")
                    if resource.file_format == "pdf" and b"%PDF-" not in prefix:
                        raise ResourceError("下载内容不是有效的 PDF 文件。")
                    if length and received != length:
                        raise ResourceError("文件未下载完整，请重试。")
                    if resource.md5 and digest.hexdigest() != resource.md5:
                        raise ResourceError("文件校验失败，请重试。")
                    if not length and resource.size and not response.headers.get("Content-Encoding") and received != resource.size:
                        raise ResourceError("下载大小与资源详情不符，请重试。")
                    os.replace(temporary, destination)
                    temporary = None
                    if progress:
                        progress(received, received)
                    return destination
            except DownloadCancelled:
                raise
            except ResourceError as error:
                failures.append(str(error))
            except requests.RequestException:
                failures.append("网络连接中断或超时，请检查网络后重试。")
            except OSError:
                raise ResourceError("无法写入文件，请检查保存目录、文件占用情况和磁盘空间。") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        detail = "；".join(dict.fromkeys(failures))
        raise ResourceError(f"所有下载节点均失败：{detail or '没有可用的下载地址。'}")
