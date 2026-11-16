"""RunPod Serverless handler for ComfyUI running from a Regional Network Volume."""

import base64
import os
import subprocess
import time
import uuid
from pathlib import Path

import boto3
import requests
import runpod

COMFY_URL = "http://127.0.0.1:8188"
INPUT_DIR = Path("/tmp/comfy_input")
OUTPUT_DIR = Path("/tmp/comfy_output")


def _safe_filename(name: str) -> Path:
    """Resolve a filename safely inside INPUT_DIR without path traversal."""
    clean = Path(name).name
    if not clean:
        raise ValueError(f"Invalid filename: {name!r}")
    return INPUT_DIR / clean


def _decode_base64_payload(raw: str) -> bytes:
    """Decode raw base64 or a data URI into bytes."""
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw)


def _download_url(url: str, dest: Path) -> None:
    """Stream a file from an HTTP/presigned URL to disk."""
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def _resample_24fps(src: Path) -> None:
    """Convert a video to 24 fps in-place if its framerate exceeds 24.5 fps."""
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1", str(src),
        ],
        capture_output=True, text=True, check=False,
    )
    rate_str = probe.stdout.strip()
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        fps = float(num) / max(1.0, float(den))
    elif rate_str:
        fps = float(rate_str)
    else:
        return

    if fps <= 24.5:
        return

    tmp = src.with_suffix(".24fps.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-r", "24", "-c:v", "libx264", "-crf", "16", "-c:a", "copy",
            str(tmp),
        ],
        check=True, capture_output=True,
    )
    tmp.replace(src)


def stage_inputs(job_input: dict) -> None:
    """Write images, videos, and generic files from the job payload into INPUT_DIR."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    for item in job_input.get("images") or []:
        dest = _safe_filename(item["name"])
        if item.get("url"):
            _download_url(item["url"], dest)
        elif item.get("image"):
            dest.write_bytes(_decode_base64_payload(item["image"]))

    for item in job_input.get("videos") or []:
        dest = _safe_filename(item["name"])
        if item.get("url"):
            _download_url(item["url"], dest)
        elif item.get("video") or item.get("data"):
            dest.write_bytes(_decode_base64_payload(item.get("video") or item["data"]))
        if item.get("resample_24fps", True):
            _resample_24fps(dest)

    for item in job_input.get("files") or []:
        dest = _safe_filename(item["name"])
        if item.get("url"):
            _download_url(item["url"], dest)
        elif item.get("data"):
            dest.write_bytes(_decode_base64_payload(item["data"]))


def submit_and_wait(workflow: dict, timeout_s: int = 1800) -> dict:
    """Queue a ComfyUI API workflow and poll /history until completion."""
    client_id = str(uuid.uuid4())
    resp = requests.post(
        f"{COMFY_URL}/prompt",
        json={"prompt": workflow, "client_id": client_id},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"ComfyUI /prompt rejected workflow ({resp.status_code}): {resp.text}")

    prompt_id = resp.json()["prompt_id"]
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        hist_resp = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
        hist_resp.raise_for_status()
        history = hist_resp.json()
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI execution error: {status.get('messages')}")
            return entry
        time.sleep(2)

    raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout_s}s")


def _s3_client():
    """Build a boto3 S3 client if all BUCKET_* env vars are configured."""
    endpoint = os.environ.get("BUCKET_ENDPOINT_URL")
    key = os.environ.get("BUCKET_ACCESS_KEY_ID")
    secret = os.environ.get("BUCKET_SECRET_ACCESS_KEY")
    bucket = os.environ.get("BUCKET_NAME")
    if not all([endpoint, key, secret, bucket]):
        return None, None
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key,
        aws_secret_access_key=secret,
    )
    return client, bucket


def collect_outputs(history_entry: dict, job_id: str) -> list[dict]:
    """Read generated output files and return S3 presigned URLs or base64 payloads."""
    s3, bucket = _s3_client()
    results = []

    for node_out in (history_entry.get("outputs") or {}).values():
        for media_list_key in ("images", "gifs", "videos"):
            for item in node_out.get(media_list_key) or []:
                filename = item.get("filename")
                if not filename or item.get("type") == "temp":
                    continue
                subfolder = item.get("subfolder") or ""
                file_path = OUTPUT_DIR / subfolder / filename
                if not file_path.is_file():
                    continue

                if s3 and bucket:
                    s3_key = f"outputs/{job_id}/{filename}"
                    s3.upload_file(str(file_path), bucket, s3_key)
                    url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": bucket, "Key": s3_key},
                        ExpiresIn=86400,
                    )
                    results.append({"filename": filename, "type": "s3_url", "url": url, "s3_key": s3_key})
                else:
                    b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
                    results.append({"filename": filename, "type": "base64", "data": b64})

    return results


def handler(event: dict) -> dict:
    """RunPod Serverless entrypoint."""
    job_input = event.get("input") or {}
    workflow = job_input.get("workflow")
    if not isinstance(workflow, dict):
        return {"error": "Missing or invalid 'input.workflow' dict"}

    stage_inputs(job_input)
    history_entry = submit_and_wait(workflow)
    outputs = collect_outputs(history_entry, event.get("id", str(uuid.uuid4())))
    return {"images": outputs}


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
