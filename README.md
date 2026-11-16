# Custom RunPod Serverless Worker (Volume-Backed ComfyUI 0.44.0)

This lightweight worker boots **your existing ComfyUI `0.44.0`, Python environment, custom nodes, and models** directly from Regional Network Volume `eo98033g16` (`/runpod-volume/runpod-slim/ComfyUI`), instead of `worker-comfyui`'s older built-in ComfyUI.

## Deploy via RunPod GitHub Integration (No Local Docker Required)

1. Create a new GitHub repository and upload the contents of this `serverless-worker/` folder (`Dockerfile`, `requirements.txt`, `start.sh`, `handler.py`) to the root of that repo.
2. In **RunPod Console → Serverless → New Endpoint** (or edit your existing endpoint):
   - Choose **GitHub Repo** as the deployment source and select your repo/branch.
   - Attach your Regional Network Volume **`eo98033g16`**.
   - Select a GPU with ≥ 32 GB VRAM (e.g., **RTX 5090 / A6000 / L40S**).
   - Set **Min Workers = `0`** and **Execution Timeout = `1800`s** (or higher).
3. *(Optional, for >10 MB HD videos)* Add your AWS S3 environment variables:
   - `BUCKET_ENDPOINT_URL` (`https://s3.<your-region>.amazonaws.com`)
   - `BUCKET_NAME`
   - `BUCKET_ACCESS_KEY_ID`
   - `BUCKET_SECRET_ACCESS_KEY`

## Payload Format

```json
{
  "input": {
    "images": [
      { "name": "face.jpg", "url": "https://..." }
    ],
    "videos": [
      { "name": "clip.mp4", "url": "https://...", "resample_24fps": true }
    ],
    "workflow": { "...": "ComfyUI API JSON" }
  }
}
```

Both `url` (presigned S3 URL) and base64 (`image` / `video` / `data`) are supported.
