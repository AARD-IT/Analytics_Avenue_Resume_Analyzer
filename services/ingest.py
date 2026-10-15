"""
Ingestion: read resume text, Google Drive sync, and the processing pipeline.

Pipeline per resume (cached by SHA-256 of the file, so the LLM is paid once):
  bytes -> hash -> already processed? skip -> text (PyMuPDF) -> Extractor (1 LLM call)
        -> normalise -> MongoDB (record + original file)
"""
import hashlib
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core import store
from core.config import Config, get_secret
from core.llm import LLMUnavailable
from services.extract import Extractor

log = logging.getLogger("ingest")
SUPPORTED = (".pdf", ".docx", ".txt")
GDOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIN_TEXT = 150


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def safe_name(name: str) -> str:
    return (re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip() or "resume")[:180]


def read_text(data: bytes, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            text, links = "", []
            try:
                import pymupdf
                with pymupdf.open(stream=data, filetype="pdf") as doc:
                    parts = []
                    for page in doc:
                        parts.append(page.get_text())
                        for ln in page.get_links():
                            uri = ln.get("uri") or ""
                            if uri.startswith(("mailto:", "tel:")) or "linkedin.com" in uri:
                                links.append(uri.replace("mailto:", "").replace("tel:", ""))
                    text = "\n".join(parts)
            except Exception as e:
                log.warning("PyMuPDF failed on %s: %s", filename, e)
            if len(text.strip()) < 100:
                from pypdf import PdfReader
                text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
            if links:
                text += "\nLINKS: " + " | ".join(dict.fromkeys(links))
            return text
        if ext == ".docx":
            import docx2txt
            return docx2txt.process(io.BytesIO(data)) or ""
        if ext == ".txt":
            return data.decode("utf-8", errors="ignore")
    except Exception as e:
        log.error("Could not read %s: %s", filename, e)
    return ""


def pdf_pages_png(data: bytes, filename: str, max_pages=4, zoom=1.6) -> list:
    """Render resume pages to PNG for the in-app viewer (works in every browser)."""
    if Path(filename).suffix.lower() != ".pdf":
        return []
    import pymupdf
    out = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            out.append(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png"))
    return out


def folder_id_from(link_or_id: str) -> str:
    s = (link_or_id or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_\-]+)", s) or re.search(r"[?&]id=([A-Za-z0-9_\-]+)", s)
    return m.group(1) if m else s


# ------------------------------------------------------------------ Drive
class Drive:
    def __init__(self):
        self.service = None
        self.error = ""
        creds = self._creds()
        if creds:
            try:
                from googleapiclient.discovery import build
                self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
            except Exception as e:
                self.error = str(e)
        else:
            self.error = "No Google service account configured ([gcp_service_account] in secrets)."

    @staticmethod
    def _creds():
        try:
            from google.oauth2 import service_account
        except ImportError:
            return None
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        info = get_secret("gcp_service_account")
        if info:
            try:
                return service_account.Credentials.from_service_account_info(dict(info), scopes=scopes)
            except Exception as e:
                log.error("bad gcp_service_account: %s", e)
        raw = get_secret("GCP_SERVICE_ACCOUNT")
        if raw:
            try:
                return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
            except Exception as e:
                log.error("bad GCP_SERVICE_ACCOUNT: %s", e)
        path = get_secret("SERVICE_ACCOUNT_FILE")
        if path and Path(path).exists():
            return service_account.Credentials.from_service_account_file(path, scopes=scopes)
        return None

    @property
    def ready(self):
        return self.service is not None

    def list_folder(self, folder_id: str) -> list:
        files, token = [], None
        while True:
            r = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=1000, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files += r.get("files", [])
            token = r.get("nextPageToken")
            if not token:
                return files

    def download(self, item: dict) -> tuple:
        from googleapiclient.http import MediaIoBaseDownload
        name, mime = item["name"], item.get("mimeType", "")
        if mime == GDOC:
            req = self.service.files().export_media(fileId=item["id"], mimeType=DOCX)
            name = Path(name).stem + ".docx"
        else:
            req = self.service.files().get_media(fileId=item["id"], supportsAllDrives=True)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return safe_name(name), buf.getvalue()


# ------------------------------------------------------------------ pipeline
def process_one(data: bytes, filename: str, extractor: Extractor, source="upload",
                drive_file_id="", force=False) -> str:
    """Returns status: ok | skipped | no_text | error:<msg>."""
    cid = sha(data)
    existing = store.candidate_exists(cid)
    if existing and not force:
        if existing.get("prompt_version") == Config.PROMPT_VERSION or not extractor.ready:
            return "skipped"
    text = read_text(data, filename)
    if len(text.strip()) < MIN_TEXT:
        store.file_status(filename, cid, "no_text", "No selectable text (scanned image PDF?)")
        return "no_text"
    rec = extractor.extract(text, filename)
    rec.update({"_id": cid, "source": source, "drive_file_id": drive_file_id})
    store.put_file(cid, filename, data)
    store.upsert_candidate(rec)
    store.file_status(filename, cid, "ok")
    return "ok"


def run_batch(items, use_llm=True, force=False, user="admin", workers=4, progress=None) -> dict:
    """
    items: iterable of (filename, bytes, source, drive_file_id)
    Runs extraction in parallel threads (I/O bound LLM calls).
    """
    extractor = Extractor(use_llm=use_llm, user=user)
    items = list(items)
    rep = {"total": len(items), "ok": 0, "skipped": 0, "no_text": 0, "errors": 0, "stopped": "",
           "llm": extractor.ready}
    stop = {"flag": False}

    def job(it):
        if stop["flag"]:
            return "skipped"
        fn, data, src, did = it
        try:
            return process_one(data, fn, extractor, src, did, force)
        except LLMUnavailable as e:
            stop["flag"] = True
            rep["stopped"] = str(e)
            return "error:" + str(e)
        except Exception as e:
            log.exception("failed %s", fn)
            store.file_status(fn, sha(data), "error", str(e))
            return "error:" + str(e)

    done = 0
    with ThreadPoolExecutor(max_workers=workers if extractor.ready else 1) as ex:
        futs = {ex.submit(job, it): it[0] for it in items}
        for f in as_completed(futs):
            st = f.result()
            key = st if st in ("ok", "skipped", "no_text") else "errors"
            rep[key] += 1
            done += 1
            if progress:
                progress(done / max(len(items), 1), f"{done}/{len(items)} · {futs[f]} → {st.split(':')[0]}")
    rep["duplicates"] = store.dedupe()
    if extractor.llm:
        rep["llm_calls"], rep["cost_usd"] = extractor.llm.calls, round(extractor.llm.cost_usd, 4)
    return rep


def sync_drive(use_llm=True, user="admin", progress=None) -> dict:
    drive = Drive()
    if not drive.ready:
        return {"error": drive.error}
    folders = store.get_settings().get("drive_folders") or []
    if not folders and Config.DEFAULT_DRIVE_FOLDER:
        folders = [Config.DEFAULT_DRIVE_FOLDER]
    known = {r["filename"] for r in store.db().candidates.find({}, {"filename": 1})}
    items, listed, errors = [], 0, []
    for fid in folders:
        try:
            for it in drive.list_folder(folder_id_from(fid)):
                listed += 1
                name = it["name"]
                if it.get("mimeType") != GDOC and Path(name).suffix.lower() not in SUPPORTED:
                    continue
                if safe_name(name) in known:
                    continue
                try:
                    fn, data = drive.download(it)
                    items.append((fn, data, "drive", it["id"]))
                except Exception as e:
                    errors.append(f"{name}: {e}")
        except Exception as e:
            errors.append(f"folder {fid}: {e}")
    rep = run_batch(items, use_llm=use_llm, user=user, progress=progress)
    rep.update(listed=listed, new_files=len(items), drive_errors=errors)
    return rep


def reprocess_all(user="admin", only_outdated=True, progress=None) -> dict:
    """Re-extract stored resumes with the current prompt/model (1 LLM call each)."""
    q = {"prompt_version": {"$ne": Config.PROMPT_VERSION}} if only_outdated else {}
    ids = [r["_id"] for r in store.db().candidates.find(q, {"_id": 1})]

    def gen():
        for cid in ids:
            fn, data = store.get_file(cid)
            if data:
                c = store.get_candidate(cid)
                yield fn, data, c.get("source", "seed"), c.get("drive_file_id", "")

    return run_batch(gen(), use_llm=True, force=True, user=user, progress=progress)
