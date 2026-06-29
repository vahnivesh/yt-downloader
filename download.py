from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import uuid
import threading
import time

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = tempfile.gettempdir()

# ---------------------------------------------------------------------------
# Copy cookies to a writable location on startup
# /etc/secrets/ is read-only on Render — yt-dlp needs to write to the file
# ---------------------------------------------------------------------------
def get_writable_cookies_path() -> str:
    src = os.environ.get("YT_COOKIES_PATH", "").strip()
    if not src or not os.path.isfile(src):
        return ""
    dest = os.path.join(DOWNLOAD_DIR, "yt_cookies.txt")
    try:
        import shutil
        shutil.copy2(src, dest)
        os.chmod(dest, 0o600)
        return dest
    except Exception as e:
        print(f"[cookies] Failed to copy cookies: {e}")
        return ""

COOKIES_PATH = get_writable_cookies_path()
if COOKIES_PATH:
    print(f"[cookies] Loaded cookies from {os.environ.get('YT_COOKIES_PATH')} → {COOKIES_PATH}")
else:
    print("[cookies] No cookies file found — YouTube may block requests.")

# ---------------------------------------------------------------------------
# Cleanup old files every 10 minutes
# ---------------------------------------------------------------------------
def cleanup_old_files():
    while True:
        time.sleep(600)
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 600:
                try:
                    os.remove(fpath)
                except Exception:
                    pass

threading.Thread(target=cleanup_old_files, daemon=True).start()


# ---------------------------------------------------------------------------
# Shared yt-dlp options that bypass YouTube bot-detection
# ---------------------------------------------------------------------------
def base_ydl_opts(extra: dict = None) -> dict:
    """
    Key bypass techniques:
      1. player_client=["ios","web"]  — uses the iOS API which is less restricted
      2. player_skip=["webpage"]      — skips the HTML page fetch that triggers bot check
      3. Realistic browser User-Agent
      4. sleep_interval / sleep_interval_requests — looks more human
      5. extractor_retries=5          — retry on transient blocks
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        # ---- YouTube bot-detection bypass ----
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "web"],   # iOS client avoids many restrictions
                "player_skip": ["webpage"],         # skip JS player page (triggers bot check)
            }
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "sleep_interval": 1,
        "sleep_interval_requests": 1,
        "extractor_retries": 5,
        "retries": 5,
        "fragment_retries": 5,
    }

    # Use the writable copy of the cookies file
    if COOKIES_PATH and os.path.isfile(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH

    if extra:
        opts.update(extra)

    return opts


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "VidDrop API is running."})


@app.route("/info", methods=["POST"])
def get_info():
    data = request.get_json()
    url = (data or {}).get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    ydl_opts = base_ydl_opts({"skip_download": True})

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Build a clean list of available formats
        formats = []
        seen = set()
        for f in info.get("formats", []):
            ext  = f.get("ext", "")
            res  = f.get("resolution") or (f"{f['height']}p" if f.get("height") else "audio")
            note = f.get("format_note", "")
            key  = (ext, res)
            if ext in ("mp4", "webm", "m4a", "mp3") and key not in seen:
                seen.add(key)
                formats.append({
                    "format_id": f.get("format_id"),
                    "ext": ext,
                    "resolution": res,
                    "note": note,
                    "filesize": f.get("filesize") or f.get("filesize_approx"),
                })

        return jsonify({
            "title":     info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "duration":  info.get("duration", 0),
            "uploader":  info.get("uploader", "Unknown"),
            "platform":  info.get("extractor_key", "Unknown"),
            "formats":   formats,
        })

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg.lower():
            return jsonify({
                "error": (
                    "YouTube is blocking this request. "
                    "Please set a cookies file on the server (see README) "
                    "or try a different video."
                )
            }), 403
        return jsonify({"error": msg}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url     = (data or {}).get("url", "").strip()
    quality = (data or {}).get("quality", "best")   # "best" | "worst" | "audio"

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    file_id     = str(uuid.uuid4())
    output_tmpl = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    if quality == "audio":
        extra = {
            "format": "bestaudio/best",
            "outtmpl": output_tmpl,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    elif quality == "worst":
        extra = {
            "format": "worst[ext=mp4]/worst",
            "outtmpl": output_tmpl,
            "merge_output_format": "mp4",
        }
    else:
        # best: prefer mp4 so no transcoding needed
        extra = {
            "format": (
                "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
                "/bestvideo[ext=mp4]+bestaudio"
                "/best[ext=mp4]/best"
            ),
            "outtmpl": output_tmpl,
            "merge_output_format": "mp4",
        }

    ydl_opts = base_ydl_opts(extra)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")

        # Find the downloaded file
        downloaded = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded or not os.path.exists(downloaded):
            return jsonify({"error": "Download failed — file not found after processing."}), 500

        ext = os.path.splitext(downloaded)[1]
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:60]
        download_name = f"{safe_title}{ext}"

        return send_file(
            downloaded,
            as_attachment=True,
            download_name=download_name,
            mimetype="audio/mpeg" if ext == ".mp3" else "video/mp4",
        )

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in" in msg or "bot" in msg.lower():
            return jsonify({
                "error": (
                    "YouTube blocked this download. "
                    "Add a cookies file to the server to fix this (see README)."
                )
            }), 403
        return jsonify({"error": msg}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
